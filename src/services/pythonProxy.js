const http = require('http');
const https = require('https');
const { backendPortCandidates } = require('../config/env');

const httpAgent = new http.Agent({ keepAlive: true, maxSockets: 32 });
const httpsAgent = new https.Agent({ keepAlive: true, maxSockets: 32 });
const DEFAULT_PROXY_TIMEOUT_MS = Number(process.env.PROXY_TIMEOUT_MS || 120000);

function normalizeLoopback(urlStr) {
  const clean = String(urlStr || '').trim().replace(/\/+$/, '');
  if (clean.startsWith('http://localhost')) {
    return clean.replace('http://localhost', 'http://127.0.0.1');
  }
  if (clean.startsWith('https://localhost')) {
    return clean.replace('https://localhost', 'https://127.0.0.1');
  }
  return clean;
}

function resolvePythonBaseUrl() {
  if (process.env.PYTHON_BASE_URL) {
    // Defensive trim + IPv4 normalization: avoids cmd.exe trailing spaces and 2s Windows ::1 delays.
    return normalizeLoopback(process.env.PYTHON_BASE_URL);
  }

  return new Promise((resolve) => {
    const candidates = backendPortCandidates.map((port) => `http://127.0.0.1:${port}`);
    let index = 0;

    const probe = () => {
      if (index >= candidates.length) {
        resolve('http://127.0.0.1:8001');
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
          } catch (_error) {
            // Ignore malformed payloads; continue probing.
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

function buildFallbackPayload(req, status = 'backend_unavailable') {
  return {
    status,
    service: 'ifspstory-node',
    path: req.originalUrl,
    message: 'Planning backend is currently unreachable. Please ensure the Python FastAPI service is running.',
    timestamp: new Date().toISOString(),
  };
}

const FORWARDED_HEADER_NAMES = [
  'authorization',
  'cookie',
  'accept',
  'x-api-key',
  'x-session-id',
  'x-request-id',
  'x-forwarded-for',
  'x-forwarded-email',
  'x-user-email',
  'x-auth-request-email',
  'x-ms-client-principal',
  'x-ms-client-principal-name',
];

function buildForwardHeaders(req, payload) {
  const headers = {};
  const hasBody = payload !== undefined && payload !== null && payload !== '' && req && req.method !== 'GET' && req.method !== 'HEAD';
  if (hasBody) {
    headers['Content-Type'] = (req.headers && req.headers['content-type']) || 'application/json';
    headers['Content-Length'] = Buffer.byteLength(payload);
  }
  if (req && req.headers) {
    for (const name of FORWARDED_HEADER_NAMES) {
      if (req.headers[name] !== undefined) {
        headers[name] = req.headers[name];
      }
    }
  }
  return headers;
}

function doRequest(targetUrl, method, body, req = null, timeoutMs = DEFAULT_PROXY_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const isHttps = targetUrl.startsWith('https');
    const client = isHttps ? https : http;
    const url = new URL(targetUrl);
    const hasBody = method !== 'GET' && method !== 'HEAD' && body !== undefined && body !== null && Object.keys(body || {}).length > 0;
    const payload = hasBody ? (typeof body === 'string' ? body : JSON.stringify(body)) : '';

    const request = client.request(
      {
        hostname: url.hostname,
        port: url.port,
        path: `${url.pathname}${url.search}`,
        method,
        agent: isHttps ? httpsAgent : httpAgent,
        headers: buildForwardHeaders(req, payload),
      },
      (response) => {
        const chunks = [];
        response.on('data', (chunk) => {
          chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
        });
        response.on('end', () => {
          const buffer = Buffer.concat(chunks);
          const contentType = String(response.headers['content-type'] || '');
          const isJson = contentType.includes('application/json');
          let parsed = null;
          if (isJson) {
            try {
              parsed = buffer.length ? JSON.parse(buffer.toString('utf8')) : {};
            } catch (_err) {
              parsed = null;
            }
          }
          resolve({
            status: response.statusCode || 200,
            headers: response.headers,
            contentType,
            isJson: isJson && parsed !== null,
            payload: parsed,
            buffer,
          });
        });
      },
    );

    request.setTimeout(timeoutMs, () => {
      const err = new Error(`Upstream request timed out after ${timeoutMs}ms`);
      err.code = 'ETIMEDOUT';
      request.destroy(err);
    });

    request.on('error', reject);
    if (payload) {
      request.write(payload);
    }
    request.end();
  });
}

async function proxyToPython(req, res) {
  const startedAt = Date.now();
  try {
    const activeBaseUrl = await resolvePythonBaseUrl();
    const targetUrl = `${activeBaseUrl}${req.originalUrl}`;
    const response = await doRequest(targetUrl, req.method, req.body, req);
    const elapsedMs = Date.now() - startedAt;

    res.setHeader('X-Proxied-By', 'ifspstory-node');
    res.setHeader('X-Upstream-Ms', String(elapsedMs));

    // Preserve transparent download / redirect / cache headers when present
    for (const h of ['content-disposition', 'cache-control', 'location', 'etag']) {
      if (response.headers[h]) {
        res.setHeader(h, response.headers[h]);
      }
    }

    if (response.isJson) {
      return res.status(response.status).json(response.payload);
    }

    if (response.contentType) {
      res.setHeader('Content-Type', response.contentType);
    }
    return res.status(response.status).send(response.buffer);
  } catch (error) {
    if (error && error.code === 'ETIMEDOUT') {
      return res.status(504).json({
        status: 'gateway_timeout',
        service: 'ifspstory-node',
        path: req.originalUrl,
        message: 'Planning backend took too long to respond. Please retry or narrow your scope.',
        timestamp: new Date().toISOString(),
      });
    }
    return res.status(503).json(buildFallbackPayload(req, 'backend_unavailable'));
  }
}

// Pipes the upstream response through unbuffered — required for SSE (text/event-stream)
// endpoints like /api/chat/stream, where full-body buffering would block incremental tokens.
async function proxyStreamToPython(req, res) {
  let activeBaseUrl;
  try {
    activeBaseUrl = await resolvePythonBaseUrl();
  } catch (_error) {
    return res.status(503).json(buildFallbackPayload(req, 'backend_unavailable'));
  }

  const targetUrl = `${activeBaseUrl}${req.originalUrl}`;
  const url = new URL(targetUrl);
  const isHttps = url.protocol === 'https:';
  const client = isHttps ? https : http;
  const payload = req.body && typeof req.body !== 'string' ? JSON.stringify(req.body) : req.body || '';

  const upstreamReq = client.request(
    {
      hostname: url.hostname,
      port: url.port,
      path: `${url.pathname}${url.search}`,
      method: req.method,
      agent: isHttps ? httpsAgent : httpAgent,
      headers: buildForwardHeaders(req, payload),
    },
    (upstreamRes) => {
      res.status(upstreamRes.statusCode || 200);
      res.setHeader('Content-Type', upstreamRes.headers['content-type'] || 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache, no-transform');
      res.setHeader('Connection', 'keep-alive');
      res.setHeader('X-Accel-Buffering', 'no');
      res.setHeader('X-Proxied-By', 'ifspstory-node');
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
      return res.status(503).json(buildFallbackPayload(req, 'backend_unavailable'));
    }
    return res.end();
  });

  req.on('close', () => upstreamReq.destroy());

  if (payload) {
    upstreamReq.write(payload);
  }
  upstreamReq.end();
}

module.exports = { proxyToPython, proxyStreamToPython, buildFallbackPayload, resolvePythonBaseUrl, normalizeLoopback };
