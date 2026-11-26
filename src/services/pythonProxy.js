const http = require('http');
const https = require('https');
const { pythonBaseUrl, backendPortCandidates } = require('../config/env');

function resolvePythonBaseUrl() {
  if (process.env.PYTHON_BASE_URL) {
    // Defensive trim: `set VAR=value && next` in cmd.exe (unquoted) captures a trailing
    // space into the value, which breaks new URL(base + path) concatenation downstream.
    return process.env.PYTHON_BASE_URL.trim();
  }

  return new Promise((resolve) => {
    const candidates = backendPortCandidates.map((port) => `http://127.0.0.1:${port}`);
    let index = 0;

    const probe = () => {
      if (index >= candidates.length) {
        resolve('http://127.0.0.1:8000');
        return;
      }

      const target = candidates[index];
      index += 1;

      const client = target.startsWith('https') ? https : http;
      const req = client.get(`${target}/api/health`, (res) => {
        const { statusCode } = res;
        let raw = '';

        res.on('data', (chunk) => {
          raw += chunk;
        });

        res.on('end', () => {
          try {
            const payload = raw ? JSON.parse(raw) : {};
            const isHealthy = statusCode >= 200 && statusCode < 400 && payload && payload.status === 'ok';
            if (isHealthy) {
              resolve(target);
              return;
            }
          } catch (error) {
            // Ignore malformed payloads; they are not valid backend health responses.
          }
          probe();
        });
      });

      req.on('error', () => probe());
      req.setTimeout(500, () => {
        req.destroy();
        probe();
      });
    };

    probe();
  });
}

function isJsonResponse(payload) {
  return payload && typeof payload === 'object' && !Buffer.isBuffer(payload);
}

function buildFallbackPayload(req, status = 'legacy_stub') {
  return {
    status,
    service: 'ifspstory-node',
    path: req.originalUrl,
    message: 'Node/Express API layer is active. The legacy Python IFSP service is not running, so this request is using compatibility fallback behavior.',
    api_contract: 'IFSP legacy compatibility',
    timestamp: new Date().toISOString(),
  };
}

const FORWARDED_HEADER_NAMES = [
  'authorization',
  'cookie',
  'x-api-key',
  'x-session-id',
  'x-forwarded-for',
  'x-forwarded-email',
  'x-user-email',
  'x-auth-request-email',
  'x-ms-client-principal',
  'x-ms-client-principal-name',
];

function buildForwardHeaders(req, payload) {
  const headers = {
    'Content-Type': (req && req.headers && req.headers['content-type']) || 'application/json',
    'Content-Length': Buffer.byteLength(payload || ''),
  };
  if (req && req.headers) {
    for (const name of FORWARDED_HEADER_NAMES) {
      if (req.headers[name] !== undefined) {
        headers[name] = req.headers[name];
      }
    }
  }
  return headers;
}

function doRequest(targetUrl, method, body, req = null) {
  return new Promise((resolve, reject) => {
    const client = targetUrl.startsWith('https') ? https : http;
    const url = new URL(targetUrl);
    const payload = body && typeof body !== 'string' ? JSON.stringify(body) : body || '';

    const request = client.request(
      {
        hostname: url.hostname,
        port: url.port,
        path: `${url.pathname}${url.search}`,
        method,
        headers: buildForwardHeaders(req, payload),
      },
      (response) => {
        let raw = '';
        response.on('data', (chunk) => {
          raw += chunk;
        });
        response.on('end', () => {
          try {
            const parsed = raw ? JSON.parse(raw) : {};
            resolve({
              status: response.statusCode || 200,
              payload: parsed,
              raw,
            });
          } catch (error) {
            resolve({
              status: response.statusCode || 200,
              payload: raw,
              raw,
            });
          }
        });
      },
    );

    request.on('error', reject);
    if (payload) {
      request.write(payload);
    }
    request.end();
  });
}

async function proxyToPython(req, res) {
  try {
    const activeBaseUrl = await resolvePythonBaseUrl();
    const targetUrl = `${activeBaseUrl}${req.originalUrl}`;
    const response = await doRequest(targetUrl, req.method, req.body, req);

    if (response.status >= 400) {
      return res.status(response.status).json({
        ...buildFallbackPayload(req, 'proxy_error'),
        python_status: response.status,
        python_response: response.payload,
      });
    }

    if (isJsonResponse(response.payload)) {
      return res.status(response.status).json(response.payload);
    }

    return res.status(response.status).json({
      data: response.payload,
      service: 'ifspstory-node',
      proxied_to: activeBaseUrl,
    });
  } catch (error) {
    return res.status(503).json(buildFallbackPayload(req, 'legacy_python_unavailable'));
  }
}

// Pipes the upstream response through unbuffered — required for SSE (text/event-stream)
// endpoints like /api/chat/stream, where doRequest()'s full-body buffering would defeat
// streaming (no incremental tokens, no keepalive pings reaching the browser).
async function proxyStreamToPython(req, res) {
  let activeBaseUrl;
  try {
    activeBaseUrl = await resolvePythonBaseUrl();
  } catch (error) {
    return res.status(503).json(buildFallbackPayload(req, 'legacy_python_unavailable'));
  }

  const targetUrl = `${activeBaseUrl}${req.originalUrl}`;
  const url = new URL(targetUrl);
  const client = url.protocol === 'https:' ? https : http;
  const payload = req.body && typeof req.body !== 'string' ? JSON.stringify(req.body) : req.body || '';

  const upstreamReq = client.request(
    {
      hostname: url.hostname,
      port: url.port,
      path: `${url.pathname}${url.search}`,
      method: req.method,
      headers: buildForwardHeaders(req, payload),
    },
    (upstreamRes) => {
      res.status(upstreamRes.statusCode || 200);
      res.setHeader('Content-Type', upstreamRes.headers['content-type'] || 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache, no-transform');
      res.setHeader('Connection', 'keep-alive');
      res.setHeader('X-Accel-Buffering', 'no'); // disable nginx buffering if fronted by one
      if (typeof res.flushHeaders === 'function') {
        res.flushHeaders();
      }

      upstreamRes.on('data', (chunk) => {
        res.write(chunk);
        if (typeof res.flush === 'function') {
          res.flush();
        }
      });
      upstreamRes.on('end', () => res.end());
      upstreamRes.on('error', () => res.end());
    },
  );

  upstreamReq.on('error', () => {
    if (!res.headersSent) {
      return res.status(503).json(buildFallbackPayload(req, 'legacy_python_unavailable'));
    }
    return res.end();
  });

  req.on('close', () => upstreamReq.destroy());

  if (payload) {
    upstreamReq.write(payload);
  }
  upstreamReq.end();
}

module.exports = { proxyToPython, proxyStreamToPython, buildFallbackPayload, resolvePythonBaseUrl };
