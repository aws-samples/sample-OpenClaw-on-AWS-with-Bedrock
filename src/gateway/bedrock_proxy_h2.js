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
const AWS_REGION = process.env.AWS_REGION || 'us-east-1';
const BEDROCK_MODEL_ID = process.env.BEDROCK_MODEL_ID || 'global.amazon.nova-2-lite-v1:0';

// Fast-path: enable/disable via env var (default: enabled)
const FAST_PATH_ENABLED = process.env.FAST_PATH_ENABLED !== 'false';
// Tenant state expiry: after this many ms without activity, tenant goes back to cold
// AgentCore idle timeout is 15 min, so we use 20 min to be safe
const TENANT_WARM_TTL_MS = parseInt(process.env.TENANT_WARM_TTL_MS || '1200000');
// Warming timeout: how long to wait for Tenant Router before falling back to fast-path
const WARMING_TIMEOUT_MS = parseInt(process.env.WARMING_TIMEOUT_MS || '8000');

function log(msg) {
  console.log(`${new Date().toISOString()} [bedrock-proxy-h2] ${msg}`);
}

// =============================================================================
// Tenant State Management
// =============================================================================

// States: 'cold' -> 'warming' -> 'warm' -> (TTL expires) -> 'cold'
const tenantState = new Map();

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

  // Primary: extract from user message text
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
          const text = (typeof agentResult === 'object' ? agentResult.response : agentResult) || 'No response';
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
 * Does not wait for response. Errors are logged and swallowed.
 */
function prewarmTenantRouter(channel, userId, message) {
  const tenantKey = getTenantKey(channel, userId);
  log(`Prewarming microVM for ${tenantKey}`);

  forwardToTenantRouter(channel, userId, message)
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

  // Async: trigger microVM prewarm (fire-and-forget)
  prewarmTenantRouter(channel, userId, userText);

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

      log(`Request: ${path} channel=${channel} user=${userId} msg=${userText.slice(0, 60)}`);

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
