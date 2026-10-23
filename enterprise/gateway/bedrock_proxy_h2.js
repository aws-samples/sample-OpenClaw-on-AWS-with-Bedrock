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
const BEDROCK_MODEL_ID = process.env.BEDROCK_MODEL_ID || 'zai.glm-5';

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

function stripTrailingConversationMetadata(text) {
  if (!text) return text;
  // Surgically remove each metadata block (header + its fenced ```json ... ```
  // code block) wherever it appears in the text, instead of deleting from the
  // header to the end of the string. OpenClaw's buildInboundUserContextPrefix()
  // PREPENDS these blocks before the real user text for many channels (incl.
  // DingTalk via dingtalk-connector) — deleting "to end of string" would wipe
  // out real commands (e.g. "/start TOKEN") that follow the metadata blocks.
  return text
    .replace(/\n{0,2}Conversation info \(untrusted metadata\):\s*```json[\s\S]*?```/gi, '')
    .replace(/\n{0,2}Sender \(untrusted metadata\):\s*```json[\s\S]*?```/gi, '')
    .trim();
}

function unwrapChannelMessage(text) {
  if (!text) return text;

  const patterns = [
    /^System:\s*\[[^\]]+\]\s*Slack DM from [^:]+:\s*([\s\S]*)$/i,
    /^System:\s*\[[^\]]+\]\s*Slack (?:message )?in #[^:]+ from [^:]+:\s*([\s\S]*)$/i,
    /^System:\s*\[[^\]]+\]\s*Telegram (?:message |DM )?from [^:]+:\s*([\s\S]*)$/i,
    /^System:\s*\[[^\]]+\]\s*Telegram (?:message )?in .+? from [^:]+:\s*([\s\S]*)$/i,
    /^System:\s*\[[^\]]+\]\s*WhatsApp (?:message |DM )?from [^:]+:\s*([\s\S]*)$/i,
    /^System:\s*\[[^\]]+\]\s*WhatsApp (?:message )?in .+? from [^:]+:\s*([\s\S]*)$/i,
    /^System:\s*\[[^\]]+\]\s*Discord DM from [^:]+:\s*([\s\S]*)$/i,
    /^System:\s*\[[^\]]+\]\s*Discord (?:message )?in #[^:]+ from [^:]+:\s*([\s\S]*)$/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return stripTrailingConversationMetadata(match[1]);
  }

  return stripTrailingConversationMetadata(text);
}

function messageText(message) {
  return (message?.content || [])
    .filter(block => typeof block?.text === 'string')
    .map(block => block.text)
    .join(' ')
    .trim();
}

function isOpenClawInternalContext(text) {
  return /^OpenClaw runtime context for the immediately preceding user message\./i.test(text)
    || (text.includes('<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>')
      && text.includes('<<<END_OPENCLAW_INTERNAL_CONTEXT>>>'));
}

function jsonMetadata(text) {
  const values = [];
  const pattern = /```json\s*([\s\S]*?)```/gi;
  let match;
  while ((match = pattern.exec(text || '')) !== null) {
    try { values.push(JSON.parse(match[1].trim())); } catch { /* ignore malformed metadata */ }
  }
  return values;
}

function extractUserMessage(body) {
  const messages = body.messages || [];
  const systemParts = body.system || [];

  // OpenClaw 2026.7+ appends a synthetic user block containing runtime context.
  // Walk backwards to the latest real user-authored block instead of routing the
  // synthetic block as channel=actions/user=a.
  let originalUserText = '';
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'user') continue;
    const candidate = messageText(messages[i]);
    if (!candidate || isOpenClawInternalContext(candidate)) continue;
    originalUserText = candidate;
    break;
  }

  let userText = originalUserText;
  let channel = 'unknown';
  let userId = 'unknown';

  // Normalize channel identifiers reported by channel plugins.
  const normalizeChannel = (value) => (value || '')
    .toString().toLowerCase().replace(/-connector$/, '');
  const applyMetadata = (meta) => {
    if (!meta || typeof meta !== 'object') return;
    const sender = meta.sender_id || meta.senderId || meta.user_id || meta.userId;
    if (userId === 'unknown' && sender) userId = String(sender);
    const candidate = meta.channel || meta.originatingChannel || meta.provider || meta.surface;
    const normalized = normalizeChannel(candidate);
    if (normalized && !['unknown', 'unkn', 'actions'].includes(normalized)) channel = normalized;
  };

  // Scan every metadata block. The first JSON block in the system prompt may be
  // unrelated tool/schema data, while inbound metadata can appear later.
  for (const meta of jsonMetadata(originalUserText)) applyMetadata(meta);
  const systemText = systemParts
    .map(part => (typeof part === 'string' ? part : part.text || ''))
    .join('\n');
  for (const meta of jsonMetadata(systemText)) applyMetadata(meta);

  // Connector-name hints are only a fallback when trusted metadata omitted channel.
  if (channel === 'unknown') {
    if (/Discord/i.test(originalUserText)) channel = 'discord';
    else if (/Telegram/i.test(originalUserText)) channel = 'telegram';
    else if (/Slack/i.test(originalUserText)) channel = 'slack';
    else if (/WhatsApp/i.test(originalUserText)) channel = 'whatsapp';
    else if (/Feishu/i.test(originalUserText)) channel = 'feishu';
    else if (/DingTalk/i.test(originalUserText)) channel = 'dingtalk';
  }

  // Legacy metadata sometimes exposes only a display label.
  if (userId === 'unknown') {
    const labelMatch = systemText.match(/"label":\s*"[^"]*\((\d{6,})\)"/);
    if (labelMatch) userId = labelMatch[1];
  }

  // Priority 1: extract from user message text (original regex)
  const slackDm = originalUserText.match(/Slack DM from ([\w]+):/i);
  const slackChan = originalUserText.match(/Slack (?:message )?in #([\w-]+).*?from ([\w]+):/i);
  const waDm = originalUserText.match(/WhatsApp (?:message |DM )?from ([\w+\-.]+):/i);
  const waGroup = originalUserText.match(/WhatsApp (?:message )?in (.+?) from ([\w+\-.]+):/i);
  const tgDm = originalUserText.match(/Telegram (?:message |DM )?from ([\w]+):/i);
  const tgGroup = originalUserText.match(/Telegram (?:message )?in (.+?) from ([\w]+):/i);
  const dcDm = originalUserText.match(/Discord DM from ([\w#]+):/i);
  const dcChan = originalUserText.match(/Discord (?:message )?in #([\w-]+).*?from ([\w#]+):/i);

  if (slackChan) { channel = 'slack'; userId = 'chan_' + slackChan[1] + '_' + slackChan[2]; }
  else if (slackDm) { channel = 'slack'; userId = 'dm_' + slackDm[1]; }
  else if (waGroup) { channel = 'whatsapp'; userId = 'grp_' + waGroup[2]; }
  else if (waDm) { channel = 'whatsapp'; userId = waDm[1]; }
  else if (tgGroup) { channel = 'telegram'; userId = 'grp_' + tgGroup[2]; }
  else if (tgDm) { channel = 'telegram'; userId = tgDm[1]; }
  else if (dcChan) { channel = 'discord'; userId = 'chan_' + dcChan[1] + '_' + dcChan[2]; }
  else if (dcDm) { channel = 'discord'; userId = 'dm_' + dcDm[1]; }

  // Last-resort legacy plain-text metadata. Reject internal pseudo-channels.
  if (userId === 'unknown') {
    const channelMatch = systemText.match(/(?:channel|source|platform)[:\s]+([\w-]+)/i);
    const fallbackChannel = normalizeChannel(channelMatch?.[1]);
    if (fallbackChannel && !['unknown', 'unkn', 'actions'].includes(fallbackChannel)) {
      channel = fallbackChannel;
    }
    const idMatch = systemText.match(/(?:sender|from|user|recipient|target)[:\s]+([\w@+\-.]+)/i);
    if (idMatch && idMatch[1].toLowerCase() !== 'actions') userId = idMatch[1];
  }

  userText = unwrapChannelMessage(originalUserText);
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
// Fargate Direct Forwarding (bypasses Tenant Router for Fargate-hosted agents)
// =============================================================================

// Cache: empId → { endpoint, tier, expiresAt }
const _fargateEndpointCache = new Map();
const _FARGATE_CACHE_TTL = 60_000; // 60 seconds

/**
 * Resolve Fargate endpoint for a user. Calls Admin Console internal API.
 * Returns { endpoint: "http://10.0.x.x:8080", tier: "executive" } or null.
 */
async function resolveFargateEndpoint(channel, userId) {
  const cacheKey = `fargate__${userId}`;
  const cached = _fargateEndpointCache.get(cacheKey);
  if (cached && Date.now() < cached.expiresAt) {
    return cached.endpoint ? cached : null;
  }

  try {
    const result = await new Promise((resolve, reject) => {
      const req = http.request({
        hostname: '127.0.0.1', port: 8099,
        path: `/api/v1/internal/resolve-fargate?channel=${encodeURIComponent(channel)}&channelUserId=${encodeURIComponent(userId)}`,
        method: 'GET', timeout: 5000,
      }, (res) => {
        let data = '';
        res.on('data', c => data += c);
        res.on('end', () => {
          try { resolve(JSON.parse(data)); } catch { resolve(null); }
        });
      });
      req.on('error', () => resolve(null));
      req.on('timeout', () => { req.destroy(); resolve(null); });
      req.end();
    });

    if (result && result.endpoint) {
      const entry = { endpoint: result.endpoint, tier: result.tier || '', expiresAt: Date.now() + _FARGATE_CACHE_TTL };
      _fargateEndpointCache.set(cacheKey, entry);
      return entry;
    }
    _fargateEndpointCache.set(cacheKey, { endpoint: '', tier: '', expiresAt: Date.now() + _FARGATE_CACHE_TTL });
    return null;
  } catch {
    return null;
  }
}

/**
 * Forward a message directly to a Fargate container's /invocations endpoint.
 * This bypasses Tenant Router entirely — the container's server.py handles
 * workspace assembly, guardrail, audit, usage tracking.
 */
function forwardToFargateContainer(endpoint, channel, userId, message) {
  return new Promise((resolve, reject) => {
    // Build tenant_id in the same format as derive_tenant_id in tenant_router.py
    const channelShort = { whatsapp: 'wa', telegram: 'tg', discord: 'dc', slack: 'sl',
      teams: 'ms', imessage: 'im', googlechat: 'gc', webchat: 'web',
      playground: 'pgnd', twin: 'twin', portal: 'port' }[channel] || channel.slice(0, 4);
    const tenantId = `${channelShort}__${userId}__fargate`;

    const url = new URL('/invocations', endpoint);
    const payload = JSON.stringify({ sessionId: tenantId, tenant_id: tenantId, message });

    const req = http.request(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': tenantId,
      },
      timeout: 300000,
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          const text = result.response || 'No response';
          resolve(String(text));
        } catch (e) {
          resolve(data || 'Parse error');
        }
      });
    });
    req.on('error', e => reject(e));
    req.on('timeout', () => { req.destroy(); reject(new Error('Fargate container timeout')); });
    req.write(payload);
    req.end();
  });
}

// =============================================================================
// Core Request Router (fast-path + warm path + Fargate direct)
// =============================================================================

/**
 * Route a request based on tenant state:
 *   Fargate -> forward directly to Fargate container (no cold start, no Tenant Router)
 *   warm    -> forward to Tenant Router (full OpenClaw, ~10s)
 *   warming -> try Tenant Router with timeout, fallback to fast-path
 *   cold    -> fast-path Bedrock (~2-3s) + async prewarm
 */
async function routeRequest(channel, userId, userText) {
  const tenantKey = getTenantKey(channel, userId);
  const status = getTenantStatus(tenantKey);

  log(`Route: ${tenantKey} status=${status} fast_path=${FAST_PATH_ENABLED}`);

  // --- Fargate: if employee's position uses Fargate, go direct (no cold start) ---
  const fargate = await resolveFargateEndpoint(channel, userId);
  if (fargate) {
    log(`Fargate direct: ${tenantKey} → ${fargate.tier} (${fargate.endpoint})`);
    try {
      const text = await forwardToFargateContainer(fargate.endpoint, channel, userId, userText);
      setTenantStatus(tenantKey, 'warm');
      return text;
    } catch (e) {
      log(`Fargate direct failed for ${tenantKey}: ${e.message}, falling back to Tenant Router`);
      // Fall through to normal routing
    }
  }

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

      log(`Request: ${path} channel=${channel} user_known=${userId !== 'unknown'}`);

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
      // Use \b word boundary instead of ^$ because Gateway wraps user text in metadata
      if (/\b(BIND|bind|Bind|绑定确认)\b/.test(userText) && pendingPairings.has(pendingKey)) {
        const pending = pendingPairings.get(pendingKey);
        if (Date.now() > pending.expiresAt) {
          pendingPairings.delete(pendingKey);
          injectResponse('Binding timed out. Please go back to the Employee Portal and generate a new QR code.');
          return;
        }
        try {
          const result = await callAdminAPI('/api/v1/bindings/pair-complete', {
            token: pending.token, channel, channelUserId: userId,
          });
          pendingPairings.delete(pendingKey);
          if (result.status === 200 && result.body.success) {
            log(`PATH C: Pairing confirmed ${channel} ${userId} → ${result.body.employeeId}`);
            // Clear binding cache so next message routes correctly
            _bindingCache.delete(`${channel}:${userId}`);
            injectResponse(`Connected! You can now chat with your AI Agent here.`);
          } else {
            injectResponse(`Binding failed: ${result.body.detail || 'please try again'}.`);
          }
        } catch (e) {
          log(`PATH C: pair-complete error: ${e.message}`);
          injectResponse('Binding error. Please try again later.');
        }
        return;
      }

      // ── Step 2b: CANCEL ────────────────────────────────────────────────
      if (/\b(CANCEL|cancel|Cancel|取消绑定)\b/.test(userText) && pendingPairings.has(pendingKey)) {
        pendingPairings.delete(pendingKey);
        injectResponse('Binding cancelled. To reconnect, go to the Employee Portal and generate a new QR code.');
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
            const action = isRebind ? 're-linking' : 'linking';
            const msg = `You are ${action} this account to [${employeeName}${positionName ? ' · ' + positionName : ''}].\n\nReply BIND to confirm, or CANCEL to abort (valid for 10 minutes).`;
            log(`PATH C: Pending pairing ${channel} ${userId} → ${employeeName}`);
            injectResponse(msg);
            return;
          }
          if (pending.status === 200 && pending.body.reason === 'already_bound_other') {
            injectResponse(`This account is already linked to ${pending.body.boundTo}. Please contact your IT admin to unlink it first.`);
            return;
          }
          log(`PATH C: pair-pending rejected reason=${pending.body?.reason || 'unknown'}`);
          injectResponse('Binding token is invalid or expired. Please return to the Employee Portal and generate a new token.');
          return;
        } catch (pairErr) {
          log(`PATH C: Pairing error: ${pairErr.message}`);
          injectResponse('Binding service is temporarily unavailable. Please try again later.');
          return;
        }
      }

      // A pairing command must never reach the LLM, even if sender metadata is
      // incomplete. This avoids exposing one-time tokens and returning model errors.
      if (pairMatch) {
        log(`PATH C: Pairing metadata incomplete channel=${channel} user_known=${userId !== 'unknown'}`);
        injectResponse('Unable to identify this messaging account. Please restart the bot connection and try binding again.');
        return;
      }

      // ── Strict binding enforcement ─────────────────────────────────────
      // Only respond to IM accounts that have been explicitly bound to an
      // employee via the Portal. Portal/web sessions (userId starts with
      // "emp-") are always allowed — they use JWT auth, not IM binding.
      if (_IM_CHANNELS_REQUIRING_BINDING.has(channel) && !userId.startsWith('emp-')) {
        const binding = await checkImBinding(channel, userId);
        if (!binding.bound) {
          log(`Binding check: ${channel}:${userId} not bound — rejecting`);
          injectResponse('This account is not linked to an employee. Please bind your account via the Employee Portal first.');
          return;
        }
        log(`Binding check: ${channel}:${userId} → ${binding.employeeId}`);
      }

      // Core routing: fast-path for cold tenants, full pipeline for warm
      const responseText = await routeRequest(channel, userId, userText);
      log(`Response complete channel=${channel}`);

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
      log(`H1 Request: channel=${channel} user_known=${userId !== 'unknown'}`);
      const responseText = await routeRequest(channel, userId, userText);
      log(`H1 Response complete channel=${channel}`);
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
