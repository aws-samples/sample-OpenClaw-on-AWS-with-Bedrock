#!/usr/bin/env node
/**
 * Bedrock Converse API HTTP/2 Proxy for OpenClaw Multi-Tenant Platform.
 *
 * Intercepts AWS SDK Bedrock Converse API calls (HTTP/2) from OpenClaw Gateway,
 * extracts user message, forwards to Tenant Router -> AgentCore -> microVM,
 * returns response in Bedrock Converse API format.
 *
 * Cold-start optimization (fast-path):
 *   When a tenant's microVM is cold, the proxy responds in ~2-3s via a direct
 *   Bedrock Converse call (no SOUL.md/memory/skills) while asynchronously
 *   triggering the full AgentCore pipeline to pre-warm the microVM.
 *   Subsequent messages use the warm microVM with full OpenClaw capabilities.
 *
 * Usage:
 *   TENANT_ROUTER_URL=http://127.0.0.1:8090 node bedrock_proxy_h2.js
 *   Then set: AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://localhost:8091
 */

const http2 = require('node:http2');
const http = require('node:http');
const https = require('node:https');
const { URL } = require('node:url');
const crypto = require('node:crypto');

const PORT = parseInt(process.env.PROXY_PORT || '8091');
const TENANT_ROUTER_URL = process.env.TENANT_ROUTER_URL || 'http://127.0.0.1:8090';
const ADMIN_CONSOLE_URL = process.env.ADMIN_CONSOLE_URL || 'http://127.0.0.1:8099';
const AWS_REGION = process.env.AWS_REGION || 'us-east-1';
const BEDROCK_MODEL_ID = process.env.BEDROCK_MODEL_ID || 'global.amazon.nova-2-lite-v1:0';

// Fast-path: enable/disable via env var (default: enabled)
const FAST_PATH_ENABLED = process.env.FAST_PATH_ENABLED !== 'false';
// Tenant state expiry: after this many ms without activity, tenant goes back to cold
// AgentCore idle timeout is 15 min, so we use 20 min to be safe
const TENANT_WARM_TTL_MS = parseInt(process.env.TENANT_WARM_TTL_MS || '1200000');
// Warming timeout: how long to wait for Tenant Router before falling back to fast-path.
// AgentCore cold start + SSM lookup + S3 workspace load = 10-15s. Use 25s to be safe.
const WARMING_TIMEOUT_MS = parseInt(process.env.WARMING_TIMEOUT_MS || '25000');

function log(msg) {
  console.log(`${new Date().toISOString()} [bedrock-proxy-h2] ${msg}`);
}

// =============================================================================
// Tenant State Management
// =============================================================================

// States: 'cold' -> 'warming' -> 'warm' -> (TTL expires) -> 'cold'
const tenantState = new Map();

// Pending IM pairing confirmations (two-step: /start TOKEN → YES/NO)
// key: `${channel}:${userId}` → { token, empName, expiresAt }
// Stored in-memory only; a proxy restart loses pending pairings (user re-scans QR to retry)
const pendingPairings = new Map();

// Binding cache: avoids a DynamoDB lookup on every IM message.
// key: `${channel}:${userId}` → { bound: bool, empId: str, expiresAt: number }
// TTL: 60 seconds. Binding changes take effect within 1 minute.
const _bindingCache = new Map();
const _BINDING_CACHE_TTL = 60_000;

// IM channels that require a valid employee binding before the bot will respond.
// Portal/web sessions use JWT auth and don't go through this check.
const _IM_CHANNELS_REQUIRING_BINDING = new Set([
  'telegram', 'discord', 'feishu', 'dingtalk', 'slack', 'teams',
  'googlechat', 'whatsapp', 'wechat', 'line', 'viber',
]);

async function checkImBinding(channel, userId) {
  const key = `${channel}:${userId}`;
  const cached = _bindingCache.get(key);
  if (cached && Date.now() < cached.expiresAt) return cached;

  try {
    const http = require('node:http');
    const result = await new Promise((resolve, reject) => {
      const req = http.request({
        hostname: '127.0.0.1', port: 8099,
        path: `/api/v1/internal/im-binding-check?channel=${encodeURIComponent(channel)}&channelUserId=${encodeURIComponent(userId)}`,
        method: 'GET',
      }, (res) => {
        let data = '';
        res.on('data', c => data += c);
        res.on('end', () => {
          try { resolve(JSON.parse(data)); } catch { resolve({ bound: false }); }
        });
      });
      req.on('error', () => resolve({ bound: false }));
      req.setTimeout(3000, () => { req.destroy(); resolve({ bound: true }); }); // fail open on timeout
      req.end();
    });
    const entry = { ...result, expiresAt: Date.now() + _BINDING_CACHE_TTL };
    _bindingCache.set(key, entry);
    return entry;
  } catch {
    return { bound: true }; // fail open — better to respond than to silently drop
  }
}

function getTenantKey(channel, userId) {
  return `${channel}__${userId}`;
}

function getTenantStatus(key) {
  const entry = tenantState.get(key);
  if (!entry) return 'cold';
  // Check TTL expiry
  if (Date.now() - entry.lastSeen > TENANT_WARM_TTL_MS) {
    tenantState.delete(key);
    return 'cold';
  }
  return entry.status;
}

function setTenantStatus(key, status) {
  tenantState.set(key, { status, lastSeen: Date.now() });
}

function touchTenant(key) {
  const entry = tenantState.get(key);
  if (entry) entry.lastSeen = Date.now();
}

// Periodic cleanup of expired entries (every 5 min)
setInterval(() => {
  const now = Date.now();
  for (const [key, entry] of tenantState) {
    if (now - entry.lastSeen > TENANT_WARM_TTL_MS) {
      tenantState.delete(key);
    }
  }
}, 300000);

// =============================================================================
// Fast-Path: Direct Bedrock Converse API call (no OpenClaw, no SOUL.md)
// =============================================================================

let bedrockClient = null;

async function initBedrockClient() {
  if (bedrockClient) return bedrockClient;
  try {
    const { BedrockRuntimeClient, ConverseCommand } = require('@aws-sdk/client-bedrock-runtime');
    bedrockClient = new BedrockRuntimeClient({ region: AWS_REGION });
    // Store ConverseCommand on the client for later use
    bedrockClient._ConverseCommand = ConverseCommand;
    log('Bedrock SDK client initialized for fast-path');
    return bedrockClient;
  } catch (e) {
    log(`Bedrock SDK not available (fast-path disabled): ${e.message}`);
    return null;
  }
}

async function fastPathBedrock(userText) {
  const client = await initBedrockClient();
  if (!client) return null;

  try {
    const cmd = new client._ConverseCommand({
      modelId: BEDROCK_MODEL_ID,
      messages: [{ role: 'user', content: [{ text: userText }] }],
      system: [{ text: 'You are a helpful AI assistant. Be concise and friendly.' }],
      inferenceConfig: { maxTokens: 1024 },
    });
    const resp = await client.send(cmd);
    const text = resp.output?.message?.content?.[0]?.text || 'No response';
    return text;
  } catch (e) {
    log(`Fast-path Bedrock error: ${e.message}`);
    return null;
  }
}

// =============================================================================
// Message Extraction (unchanged from original)
// =============================================================================

function extractUserMessage(body) {
  const messages = body.messages || [];
  const systemParts = body.system || [];

  let userText = '';
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'user') {
      const content = messages[i].content || [];
      userText = content
        .filter(b => b.text)
        .map(b => b.text)
        .join(' ')
        .trim();
      break;
    }
  }

  let channel = 'unknown';
  let userId = 'unknown';

  // Priority 0: Extract from OpenClaw's JSON metadata in message text
  // OpenClaw embeds sender info as JSON in the conversation context
  try {
    const jsonMatch = userText.match(/```json\s*\n([\s\S]*?)\n```/);
    if (jsonMatch) {
      const meta = JSON.parse(jsonMatch[1]);
      if (meta.sender_id) {
        userId = meta.sender_id;
        // Detect channel from metadata — skip placeholder values like 'unknown'/'unkn'
        const metaChannel = (meta.channel || '').toLowerCase();
        if (metaChannel && metaChannel !== 'unknown' && metaChannel !== 'unkn') {
          channel = metaChannel;
        } else if (meta.channel_type) {
          channel = meta.channel_type.toLowerCase();
        } else if (userText.includes('Discord')) channel = 'discord';
        else if (userText.includes('Telegram')) channel = 'telegram';
        else if (userText.includes('Slack')) channel = 'slack';
        else if (userText.includes('WhatsApp')) channel = 'whatsapp';
        else if (userText.includes('Feishu') || userText.includes('feishu')) channel = 'feishu';
        // Heuristic fallback: detect channel by sender_id format when no keyword found
        // Telegram user IDs are 7-12 digits; Discord IDs are 17-19 digits; WhatsApp uses +phone
        else if (/^\d{7,12}$/.test(meta.sender_id)) channel = 'telegram';
        else if (/^\d{17,19}$/.test(meta.sender_id)) channel = 'discord';
        else if (/^\+\d{7,15}$/.test(meta.sender_id)) channel = 'whatsapp';
        else if (/^ou_[a-zA-Z0-9]+$/.test(meta.sender_id)) channel = 'feishu'; // Feishu Open User ID
      }
    }
  } catch (e) { /* JSON parse failed, fall through to regex */ }

  // Also try system prompt JSON metadata
  try {
    const sysText = systemParts.map(p => (typeof p === 'string' ? p : p.text || '')).join(' ');
    const sysJsonMatch = sysText.match(/```json\s*\n([\s\S]*?)\n```/);
    if (sysJsonMatch && userId === 'unknown') {
      const meta = JSON.parse(sysJsonMatch[1]);
      if (meta.sender_id) userId = meta.sender_id;
      const sysMetaChannel = (meta.channel || '').toLowerCase();
      if (sysMetaChannel && sysMetaChannel !== 'unknown' && sysMetaChannel !== 'unkn') {
        channel = sysMetaChannel;
      }
    }
    // Also try "label": "pitchshow (1484960930608578580)" format
    if (userId === 'unknown') {
      const labelMatch = sysText.match(/"label":\s*"[^"]*\((\d{10,})\)"/);
      if (labelMatch) userId = labelMatch[1];
      const chanMatch = sysText.match(/"channel":\s*"(\w+)"/i);
      if (chanMatch) channel = chanMatch[1].toLowerCase();
    }
  } catch (e) { /* fall through */ }

  // Priority 1: extract from user message text (original regex)
  const slackDm = userText.match(/Slack DM from ([\w]+):/i);
  const slackChan = userText.match(/Slack (?:message )?in #([\w-]+).*?from ([\w]+):/i);
  const waDm = userText.match(/WhatsApp (?:message |DM )?from ([\w+\-.]+):/i);
  const waGroup = userText.match(/WhatsApp (?:message )?in (.+?) from ([\w+\-.]+):/i);
  const tgDm = userText.match(/Telegram (?:message |DM )?from ([\w]+):/i);
  const tgGroup = userText.match(/Telegram (?:message )?in (.+?) from ([\w]+):/i);
  const dcDm = userText.match(/Discord DM from ([\w#]+):/i);
  const dcChan = userText.match(/Discord (?:message )?in #([\w-]+).*?from ([\w#]+):/i);

  if (slackChan) { channel = 'slack'; userId = 'chan_' + slackChan[1] + '_' + slackChan[2]; }
  else if (slackDm) { channel = 'slack'; userId = 'dm_' + slackDm[1]; }
  else if (waGroup) { channel = 'whatsapp'; userId = 'grp_' + waGroup[2]; }
  else if (waDm) { channel = 'whatsapp'; userId = waDm[1]; }
  else if (tgGroup) { channel = 'telegram'; userId = 'grp_' + tgGroup[2]; }
  else if (tgDm) { channel = 'telegram'; userId = tgDm[1]; }
  else if (dcChan) { channel = 'discord'; userId = 'chan_' + dcChan[1] + '_' + dcChan[2]; }
  else if (dcDm) { channel = 'discord'; userId = 'dm_' + dcDm[1]; }

  // Fallback: system prompt regex
  if (userId === 'unknown') {
    const systemText = systemParts
      .map(p => (typeof p === 'string' ? p : p.text || ''))
      .join(' ');
    const chMatch = systemText.match(/(?:channel|source|platform)[:\s]+(\w+)/i);
    if (chMatch) channel = chMatch[1].toLowerCase();
    const idMatch = systemText.match(/(?:sender|from|user|recipient|target)[:\s]+([\w@+\-.]+)/i);
    if (idMatch) userId = idMatch[1];
    if (userId === 'unknown') {
      userId = 'sys-' + crypto.createHash('md5').update(systemText.slice(0, 500)).digest('hex').slice(0, 12);
    }
  }

  return { userText, channel, userId };
}

// =============================================================================
// Tenant Router Forwarding
// =============================================================================

function forwardToTenantRouter(channel, userId, message) {
  return new Promise((resolve, reject) => {
    const url = new URL('/route', TENANT_ROUTER_URL);
    const payload = JSON.stringify({ channel, user_id: userId, message });

    const req = http.request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      timeout: 300000,
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          const agentResult = result.response || {};
          let text = (typeof agentResult === 'object' ? agentResult.response : agentResult) || 'No response';
          // Handle V29+ Gateway mode: server.py may return raw Python dict str or nested result
          if (typeof text === 'string' && (text.includes("'runId'") || text.includes('"runId"'))) {
            try {
              const parsed = JSON.parse(text.replace(/'/g, '"'));
              const payloads = (parsed.result || parsed).payloads || [];
              if (payloads[0] && payloads[0].text) text = payloads[0].text;
            } catch(e) {
              const m = text.match(/'text':\s*'((?:[^'\\]|\\.)*)'/);
              if (m) text = m[1].replace(/\\n/g, '\n').replace(/\\t/g, '\t');
            }
          } else if (typeof agentResult === 'object' && agentResult.result) {
            const payloads = (agentResult.result.payloads || []);
            if (payloads[0] && payloads[0].text) text = payloads[0].text;
          }
          resolve(String(text));
        } catch (e) {
          resolve(data || 'Parse error');
        }
      });
    });
    req.on('error', e => reject(e));
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    req.write(payload);
    req.end();
  });
}

/**
 * Fire-and-forget: trigger Tenant Router to start microVM prewarming.
 * Uses a lightweight warmup message instead of the user's actual message
 * to avoid polluting OpenClaw's conversation history with a "ghost" message.
 * Does not wait for response. Errors are logged and swallowed.
 */
const WARMUP_MESSAGE = '[SYSTEM] Session warmup - please respond with OK';

function prewarmTenantRouter(channel, userId) {
  const tenantKey = getTenantKey(channel, userId);
  log(`Prewarming microVM for ${tenantKey}`);

  forwardToTenantRouter(channel, userId, WARMUP_MESSAGE)
    .then(() => {
      setTenantStatus(tenantKey, 'warm');
      log(`Prewarm complete: ${tenantKey} -> warm`);
    })
    .catch(e => {
      log(`Prewarm failed for ${tenantKey}: ${e.message}`);
      // Stay in 'warming' state; next request will retry
    });
}

/**
 * Try Tenant Router with a timeout. If it responds in time, great.
 * If not, return null so caller can fall back to fast-path.
 */
function tryTenantRouterWithTimeout(channel, userId, message, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), timeoutMs);

    forwardToTenantRouter(channel, userId, message)
      .then(text => {
        clearTimeout(timer);
        resolve(text);
      })
      .catch(() => {
        clearTimeout(timer);
        resolve(null);
      });
  });
}

// =============================================================================
// Core Request Router (fast-path + warm path)
// =============================================================================

/**
 * Route a request based on tenant state:
 *   warm    -> forward to Tenant Router (full OpenClaw, ~10s)
 *   warming -> try Tenant Router with timeout, fallback to fast-path
 *   cold    -> fast-path Bedrock (~2-3s) + async prewarm
 */
async function routeRequest(channel, userId, userText) {
  const tenantKey = getTenantKey(channel, userId);
  const status = getTenantStatus(tenantKey);

  log(`Route: ${tenantKey} status=${status} fast_path=${FAST_PATH_ENABLED}`);

  // --- Warm: microVM is running, use full OpenClaw pipeline ---
  if (status === 'warm') {
    touchTenant(tenantKey);
    const text = await forwardToTenantRouter(channel, userId, userText);
    return text;
  }

  // --- Fast-path disabled: always go through Tenant Router ---
  if (!FAST_PATH_ENABLED) {
    if (status === 'cold') setTenantStatus(tenantKey, 'warming');
    const text = await forwardToTenantRouter(channel, userId, userText);
    setTenantStatus(tenantKey, 'warm');
    return text;
  }

  // --- Warming: microVM might be ready, try with timeout ---
  if (status === 'warming') {
    const text = await tryTenantRouterWithTimeout(channel, userId, userText, WARMING_TIMEOUT_MS);
    if (text) {
      setTenantStatus(tenantKey, 'warm');
      return text;
    }
    // Timeout: fall through to fast-path
    log(`Warming timeout for ${tenantKey}, using fast-path`);
    const fastText = await fastPathBedrock(userText);
    if (fastText) return fastText;
    // Fast-path also failed: wait for full pipeline
    const fullText = await forwardToTenantRouter(channel, userId, userText);
    setTenantStatus(tenantKey, 'warm');
    return fullText;
  }

  // --- Cold: first request for this tenant ---
  setTenantStatus(tenantKey, 'warming');

  // Async: trigger microVM prewarm with lightweight warmup message (fire-and-forget)
  // Uses a system message instead of user's actual message to avoid ghost conversation history
  prewarmTenantRouter(channel, userId);

  // Sync: fast-path direct Bedrock call (~2-3s)
  const fastText = await fastPathBedrock(userText);
  if (fastText) {
    log(`Fast-path response for ${tenantKey}: ${fastText.slice(0, 60)}`);
    return fastText;
  }

  // Fast-path failed (SDK not available or Bedrock error): wait for full pipeline
  log(`Fast-path unavailable for ${tenantKey}, waiting for Tenant Router`);
  const fullText = await forwardToTenantRouter(channel, userId, userText);
  setTenantStatus(tenantKey, 'warm');
  return fullText;
}

// =============================================================================
// Response Builders (Bedrock Converse API format)
// =============================================================================

function buildConverseResponse(text) {
  return {
    output: {
      message: {
        role: 'assistant',
        content: [{ text }],
      },
    },
    stopReason: 'end_turn',
    usage: { inputTokens: 0, outputTokens: text.split(/\s+/).length, totalTokens: text.split(/\s+/).length },
    metrics: { latencyMs: 0 },
  };
}

/**
 * Build AWS eventstream binary frames for ConverseStream response.
 * Wire format per event: [total_len:4][headers_len:4][prelude_crc:4][headers][payload][message_crc:4]
 */
function buildEventStream(text) {
  const events = [];

  function crc32(buf) {
    const T = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let j = 0; j < 8; j++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      T[i] = c;
    }
    let crc = 0xFFFFFFFF;
    for (let i = 0; i < buf.length; i++) crc = T[(crc ^ buf[i]) & 0xFF] ^ (crc >>> 8);
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }

  function encodeHeaders(h) {
    const parts = [];
    for (const [k, v] of Object.entries(h)) {
      const kb = Buffer.from(k), vb = Buffer.from(v);
      const b = Buffer.alloc(1 + kb.length + 1 + 2 + vb.length);
      let o = 0;
      b.writeUInt8(kb.length, o); o += 1;
      kb.copy(b, o); o += kb.length;
      b.writeUInt8(7, o); o += 1; // type 7 = string
      b.writeUInt16BE(vb.length, o); o += 2;
      vb.copy(b, o);
      parts.push(b);
    }
    return Buffer.concat(parts);
  }

  function makeEvent(type, payload) {
    const hdrs = {
      ':event-type': type,
      ':content-type': 'application/json',
      ':message-type': 'event',
    };
    const hBuf = encodeHeaders(hdrs);
    const pBuf = Buffer.from(JSON.stringify(payload));
    const total = 12 + hBuf.length + pBuf.length + 4;
    const buf = Buffer.alloc(total);
    let o = 0;
    buf.writeUInt32BE(total, o); o += 4;
    buf.writeUInt32BE(hBuf.length, o); o += 4;
    buf.writeUInt32BE(crc32(buf.slice(0, 8)), o); o += 4;
    hBuf.copy(buf, o); o += hBuf.length;
    pBuf.copy(buf, o); o += pBuf.length;
    buf.writeUInt32BE(crc32(buf.slice(0, o)), o);
    return buf;
  }

  events.push(makeEvent('messageStart', { role: 'assistant' }));
  events.push(makeEvent('contentBlockStart', { contentBlockIndex: 0, start: {} }));
  events.push(makeEvent('contentBlockDelta', { contentBlockIndex: 0, delta: { text } }));
  events.push(makeEvent('contentBlockStop', { contentBlockIndex: 0 }));
  events.push(makeEvent('messageStop', { stopReason: 'end_turn' }));
  const tc = text.split(/\s+/).length;
  events.push(makeEvent('metadata', {
    usage: { inputTokens: 0, outputTokens: tc, totalTokens: tc },
    metrics: { latencyMs: 0 },
  }));

  return events;
}

// =============================================================================
// HTTP/2 Server (main — handles AWS SDK Bedrock calls from OpenClaw Gateway)
// =============================================================================

const server = http2.createServer();

server.on('stream', (stream, headers) => {
  const method = headers[':method'];
  const path = headers[':path'] || '/';

  if (method === 'GET' && (path === '/ping' || path === '/')) {
    stream.respond({ ':status': 200, 'content-type': 'application/json' });
    stream.end(JSON.stringify({
      status: 'healthy',
      service: 'bedrock-proxy-h2',
      fastPath: FAST_PATH_ENABLED,
      tenants: tenantState.size,
    }));
    return;
  }

  if (method !== 'POST') {
    stream.respond({ ':status': 405 });
    stream.end('Method not allowed');
    return;
  }

  const isStream = path.includes('converse-stream');
  let body = '';

  stream.on('data', chunk => body += chunk);
  stream.on('end', async () => {
    try {
      const parsed = JSON.parse(body);
      const { userText, channel, userId } = extractUserMessage(parsed);

      log(`DEBUG-SYS: ${JSON.stringify((parsed.system||[]).map(s=>typeof s==='string'?s:s.text||'')).slice(0,500)}`);
      log(`DEBUG-MSG: ${userText.slice(0,300)}`);
      log(`Request: ${path} channel=${channel} user=${userId} msg=${userText.slice(0, 60)}`);

      // =====================================================================
      // PATH B: Admin Assistant bypass — proxy directly to real Bedrock
      // Admin sessions should NOT go through Tenant Router → AgentCore.
      // Instead, forward the original Bedrock request to the real endpoint.
      // This preserves OpenClaw's full system prompt (SOUL.md) and tool defs.
      // =====================================================================
      const sessionId = parsed.system?.map(s => typeof s === 'string' ? s : s.text || '').join(' ') || '';
      const isAdmin = (userId === 'admin'); // Only admin console sessions use userId='admin'; Discord SOUL content must not trigger PATH B

      if (isAdmin) {
        log(`PATH B: Admin bypass — proxying to real Bedrock (skip Tenant Router)`);
        try {
          const client = await initBedrockClient();
          if (client) {
            // Extract model ID from the URL path
            const modelMatch = path.match(/model\/([^/]+)/);
            const modelId = modelMatch ? decodeURIComponent(modelMatch[1]) : BEDROCK_MODEL_ID;

            const cmd = new client._ConverseCommand({
              modelId,
              messages: parsed.messages || [],
              system: parsed.system || [],
              inferenceConfig: parsed.inferenceConfig || { maxTokens: 4096 },
              toolConfig: parsed.toolConfig || undefined,
            });
            const resp = await client.send(cmd);
            const content = resp.output?.message?.content || [];

            if (isStream) {
              // Build event stream from response
              const texts = content.filter(c => c.text).map(c => c.text);
              const toolUses = content.filter(c => c.toolUse);
              // For streaming, send text + tool use blocks
              const fullResp = JSON.stringify({
                output: resp.output,
                stopReason: resp.stopReason,
                usage: resp.usage,
              });
              stream.respond({ ':status': 200, 'content-type': 'application/vnd.amazon.eventstream' });
              for (const e of buildEventStream(texts.join('\n') || JSON.stringify(content))) stream.write(e);
              stream.end();
            } else {
              stream.respond({ ':status': 200, 'content-type': 'application/json' });
              stream.end(JSON.stringify({
                output: resp.output,
                stopReason: resp.stopReason,
                usage: resp.usage,
                metrics: { latencyMs: 0 },
              }));
            }
            return;
          }
        } catch (adminErr) {
          log(`Admin bypass error: ${adminErr.message}, falling through to normal routing`);
        }
      }

      // =====================================================================
      // PATH A: Employee agents — route through Tenant Router → AgentCore
      // =====================================================================

      if (!userText) {
        const noMsg = "I didn't receive a message.";
        if (isStream) {
          stream.respond({ ':status': 200, 'content-type': 'application/vnd.amazon.eventstream' });
          for (const e of buildEventStream(noMsg)) stream.write(e);
          stream.end();
        } else {
          stream.respond({ ':status': 200, 'content-type': 'application/json' });
          stream.end(JSON.stringify(buildConverseResponse(noMsg)));
        }
        return;
      }

      // =====================================================================
      // PATH C: IM Self-Service Pairing — two-step confirmation flow
      //
      // Step 1: /start TOKEN → pair-pending (validate) → inject "reply YES to confirm"
      //         Pending state stored in memory (pendingPairings Map, 10 min TTL)
      // Step 2: YES → pair-complete → inject success
      //         NO  → cancel → inject cancel
      //
      // Safety: errors fall through to normal routing (employee gets agent response)
      // =====================================================================

      // Helper: call Admin Console API (internal only, no auth needed)
      const callAdminAPI = (path, payload) => new Promise((resolve, reject) => {
        const http = require('node:http');
        const body = JSON.stringify(payload);
        const req = http.request({
          hostname: '127.0.0.1', port: 8099, path,
          method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        }, (res) => {
          let data = '';
          res.on('data', c => data += c);
          res.on('end', () => {
            try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
            catch { resolve({ status: res.statusCode, body: {} }); }
          });
        });
        req.on('error', reject);
        req.setTimeout(5000, () => { req.destroy(); reject(new Error('timeout')); });
        req.write(body);
        req.end();
      });

      // Helper: inject a fake Bedrock response without calling AgentCore
      const injectResponse = (text) => {
        if (isStream) {
          stream.respond({ ':status': 200, 'content-type': 'application/vnd.amazon.eventstream' });
          for (const e of buildEventStream(text)) stream.write(e);
          stream.end();
        } else {
          stream.respond({ ':status': 200, 'content-type': 'application/json' });
          stream.end(JSON.stringify(buildConverseResponse(text)));
        }
      };

      const pendingKey = `${channel}:${userId}`;
      const msgTrim = userText.trim();

      // ── Step 2a: BIND confirmation (not YES/NO to avoid intercepting normal conversation) ──
      if (/^(bind|BIND|Bind|绑定确认)$/.test(msgTrim) && pendingPairings.has(pendingKey)) {
        const pending = pendingPairings.get(pendingKey);
        if (Date.now() > pending.expiresAt) {
          pendingPairings.delete(pendingKey);
          injectResponse('⏱ 绑定超时，请回到 Portal 重新生成二维码。');
          return;
        }
        try {
          const result = await callAdminAPI('/api/v1/bindings/pair-complete', {
            token: pending.token, channel, channelUserId: userId,
          });
          pendingPairings.delete(pendingKey);
          if (result.status === 200 && result.body.success) {
            log(`PATH C: Pairing confirmed ${channel} ${userId} → ${result.body.employeeId}`);
            injectResponse(`✅ 绑定成功！你现在可以在这里直接与 AI Agent 对话了。`);
          } else {
            injectResponse(`绑定失败：${result.body.detail || '请重试'}。`);
          }
        } catch (e) {
          log(`PATH C: pair-complete error: ${e.message}`);
          injectResponse('绑定时出错，请稍后重试。');
        }
        return;
      }

      // ── Step 2b: CANCEL ────────────────────────────────────────────────
      if (/^(cancel|CANCEL|Cancel|取消绑定)$/.test(msgTrim) && pendingPairings.has(pendingKey)) {
        pendingPairings.delete(pendingKey);
        injectResponse('已取消绑定。如需重新连接请回到员工门户重新生成二维码。');
        return;
      }

      // ── Step 1: /start TOKEN ───────────────────────────────────────────
      const pairMatch = userText.match(/\/start\s+([A-Za-z0-9]{10,16})/);
      if (pairMatch && userId !== 'unknown' && channel !== 'unknown') {
        const token = pairMatch[1].toUpperCase();
        try {
          const pending = await callAdminAPI('/api/v1/bindings/pair-pending', {
            token, channel, channelUserId: userId,
          });
          if (pending.status === 200 && pending.body.valid) {
            const { employeeName, positionName, isRebind } = pending.body;
            pendingPairings.set(pendingKey, {
              token,
              empName: employeeName,
              expiresAt: Date.now() + 10 * 60 * 1000,
            });
            const action = isRebind ? '重新绑定' : '绑定';
            const msg = `你正在将此账号${action}到 [${employeeName}${positionName ? ' · ' + positionName : ''}]。\n\n回复 BIND 确认绑定，回复 CANCEL 取消（10 分钟内有效）。`;
            log(`PATH C: Pending pairing ${channel} ${userId} → ${employeeName}`);
            injectResponse(msg);
            return;
          }
          if (pending.status === 200 && pending.body.reason === 'already_bound_other') {
            injectResponse(`此账号已绑定到 ${pending.body.boundTo}，请联系 IT 管理员解绑后再试。`);
            return;
          }
          // Token invalid/expired — fall through to normal routing
          log(`PATH C: pair-pending invalid (${pending.body?.reason}), routing normally`);
        } catch (pairErr) {
          log(`PATH C: Pairing error (falling through): ${pairErr.message}`);
        }
      }

      // ── Strict binding enforcement ─────────────────────────────────────
      // Only respond to IM accounts that have been explicitly bound to an
      // employee via the Portal. Portal/web sessions (userId starts with
      // "emp-") are always allowed — they use JWT auth, not IM binding.
      if (_IM_CHANNELS_REQUIRING_BINDING.has(channel) && !userId.startsWith('emp-')) {
        const binding = await checkImBinding(channel, userId);
        if (!binding.bound) {
          log(`Binding check: ${channel}:${userId} not bound — rejecting`);
          injectResponse('此账号尚未绑定到企业员工账户。请通过员工门户（Portal）扫码绑定后再使用。');
          return;
        }
        log(`Binding check: ${channel}:${userId} → ${binding.employeeId}`);
      }

      // Core routing: fast-path for cold tenants, full pipeline for warm
      const responseText = await routeRequest(channel, userId, userText);
      log(`Response: ${responseText.slice(0, 80)}`);

      if (isStream) {
        stream.respond({ ':status': 200, 'content-type': 'application/vnd.amazon.eventstream' });
        for (const e of buildEventStream(responseText)) stream.write(e);
        stream.end();
      } else {
        stream.respond({ ':status': 200, 'content-type': 'application/json' });
        stream.end(JSON.stringify(buildConverseResponse(responseText)));
      }
    } catch (e) {
      log(`Error: ${e.message}`);
      stream.respond({ ':status': 500, 'content-type': 'application/json' });
      stream.end(JSON.stringify({ message: e.message }));
    }
  });
});

// =============================================================================
// HTTP/1.1 Server (health checks + curl testing)
// =============================================================================

const h1Server = http.createServer((req, res) => {
  if (req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: 'healthy',
      service: 'bedrock-proxy-h2',
      fastPath: FAST_PATH_ENABLED,
      tenants: tenantState.size,
      note: 'Use HTTP/2 for Bedrock API',
    }));
    return;
  }

  let body = '';
  req.on('data', chunk => body += chunk);
  req.on('end', async () => {
    try {
      const parsed = JSON.parse(body);
      const { userText, channel, userId } = extractUserMessage(parsed);
      log(`H1 Request: channel=${channel} user=${userId} msg=${userText.slice(0, 60)}`);
      const responseText = await routeRequest(channel, userId, userText);
      log(`H1 Response: ${responseText.slice(0, 80)}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(buildConverseResponse(responseText)));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ message: e.message }));
    }
  });
});

// =============================================================================
// Startup
// =============================================================================

server.listen(PORT, '0.0.0.0', () => {
  log(`HTTP/2 proxy listening on port ${PORT}`);
  log(`Tenant Router: ${TENANT_ROUTER_URL}`);
  log(`Fast-path: ${FAST_PATH_ENABLED ? 'ENABLED' : 'DISABLED'} (model: ${BEDROCK_MODEL_ID})`);
  log(`Tenant warm TTL: ${TENANT_WARM_TTL_MS / 1000}s, warming timeout: ${WARMING_TIMEOUT_MS}ms`);
});

h1Server.listen(PORT + 1, '0.0.0.0', () => {
  log(`HTTP/1.1 health check on port ${PORT + 1}`);
});

// Pre-initialize Bedrock client at startup (non-blocking)
if (FAST_PATH_ENABLED) {
  initBedrockClient().catch(() => {});
}
