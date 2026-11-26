const express = require('express');
const { proxyToPython } = require('../services/pythonProxy');

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

router.get('/api/config', (req, res) => proxyToPython(req, res));

module.exports = router;
