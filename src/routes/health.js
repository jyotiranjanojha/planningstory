const express = require('express');
const { proxyToPython, resolvePythonBaseUrl } = require('../services/pythonProxy');

const router = express.Router();

router.get('/health', (_req, res) => {
  res.status(200).json({
    status: 'ok',
    service: 'ifspstory-node',
    environment: process.env.NODE_ENV || 'development',
    timestamp: new Date().toISOString(),
  });
});

router.get('/api/health', (_req, res) => {
  res.status(200).json({
    status: 'ok',
    service: 'ifspstory-node',
    base_dir: process.cwd(),
  });
});

// Enterprise Kubernetes / Rancher readiness probe: verifies Node Gateway AND internal Python/DuckDB backend.
router.get('/api/ready', async (_req, res) => {
  try {
    const baseUrl = await resolvePythonBaseUrl();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const upstream = await fetch(`${baseUrl}/api/v2/health`, { signal: controller.signal });
    clearTimeout(timer);

    if (!upstream.ok) {
      return res.status(503).json({
        status: 'degraded',
        service: 'ifspstory-node',
        backend_status: upstream.status,
      });
    }

    const backend = await upstream.json();
    return res.status(200).json({
      status: 'ready',
      service: 'ifspstory-node',
      backend,
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    return res.status(503).json({
      status: 'not_ready',
      service: 'ifspstory-node',
      error: err.message,
      timestamp: new Date().toISOString(),
    });
  }
});

router.get('/api/config', (req, res) => proxyToPython(req, res));

module.exports = router;
