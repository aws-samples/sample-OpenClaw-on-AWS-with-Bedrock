#!/usr/bin/env node
/**
 * Bedrock Converse API HTTP/2 Proxy for OpenClaw Multi-Tenant Platform.
 *
 * Intercepts AWS SDK Bedrock Converse API calls (HTTP/2) from OpenClaw Gateway,
 * extracts user message, forwards to Tenant Router → AgentCore → microVM,
 * returns response in Bedrock Converse API format.
 *
 * Usage:
 *   TENANT_ROUTER_URL=http://127.0.0.1:8090 node bedrock_proxy_h2.js
 *   Then set: AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://localhost:8091
 */

const http2 = require('node:http2');
const http = require('node:http');
const { URL } = require('node:url');

const PORT = parseInt(process.env.PROXY_PORT || '8091');
const TENANT_ROUTER_URL = process.env.TENANT_ROUTER_URL || 'http://127.0.0.1:8090';

function log(msg) {
  console.log(`${new Date().toISOString()} [bedrock-proxy-h2] ${msg}`);
}

function extractUserMessage(body) {
  const messages = body.messages || [];
  const systemParts = body.system || [];

  // Last user message
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

  // Extract channel/sender from system prompt
  const systemText = systemParts
    .map(p => (typeof p === 'string' ? p : p.text || ''))
    .join(' ');

  let channel = 'unknown';
  let userId = 'unknown';

  const chMatch = systemText.match(/(?:channel|source|platform)[:\s]+(\w+)/i);
  if (chMatch) channel = chMatch[1].toLowerCase();

  const idMatch = systemText.match(/(?:sender|from|user|recipient|target)[:\s]+([\w@+\-.]+)/i);
  if (idMatch) userId = idMatch[1];

  if (userId === 'unknown') {
    // Hash system prompt for stable tenant_id
    const crypto = require('node:crypto');
    userId = 'sys-' + crypto.createHash('md5').update(systemText.slice(0, 500)).digest('hex').slice(0, 12);
  }

  return { userText, channel, userId };
}

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

// Create HTTP/2 server (cleartext, no TLS — for local use)
const server = http2.createServer();

server.on('stream', (stream, headers) => {
  const method = headers[':method'];
  const path = headers[':path'] || '/';

  if (method === 'GET' && (path === '/ping' || path === '/')) {
    stream.respond({ ':status': 200, 'content-type': 'application/json' });
    stream.end(JSON.stringify({ status: 'healthy', service: 'bedrock-proxy-h2' }));
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
        const resp = buildConverseResponse("I didn't receive a message.");
        stream.respond({ ':status': 200, 'content-type': 'application/json' });
        stream.end(JSON.stringify(resp));
        return;
      }

      const responseText = await forwardToTenantRouter(channel, userId, userText);
      log(`Response: ${responseText.slice(0, 80)}`);

      if (isStream) {
        // Bedrock ConverseStream uses application/vnd.amazon.eventstream
        // For simplicity, return non-streaming response (OpenClaw handles both)
        stream.respond({ ':status': 200, 'content-type': 'application/json' });
        stream.end(JSON.stringify(buildConverseResponse(responseText)));
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

// Also listen on HTTP/1.1 for health checks and curl testing
const h1Server = http.createServer((req, res) => {
  if (req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'healthy', service: 'bedrock-proxy-h2', note: 'Use HTTP/2 for Bedrock API' }));
    return;
  }

  let body = '';
  req.on('data', chunk => body += chunk);
  req.on('end', async () => {
    try {
      const parsed = JSON.parse(body);
      const { userText, channel, userId } = extractUserMessage(parsed);
      log(`H1 Request: channel=${channel} user=${userId} msg=${userText.slice(0, 60)}`);
      const responseText = await forwardToTenantRouter(channel, userId, userText);
      log(`H1 Response: ${responseText.slice(0, 80)}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(buildConverseResponse(responseText)));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ message: e.message }));
    }
  });
});

server.listen(PORT, '0.0.0.0', () => {
  log(`HTTP/2 proxy listening on port ${PORT}`);
  log(`Tenant Router: ${TENANT_ROUTER_URL}`);
});

// HTTP/1.1 on PORT+1 for health checks
h1Server.listen(PORT + 1, '0.0.0.0', () => {
  log(`HTTP/1.1 health check on port ${PORT + 1}`);
});
