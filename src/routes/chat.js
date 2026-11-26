const express = require('express');
const { proxyToPython, proxyStreamToPython } = require('../services/pythonProxy');

const router = express.Router();

router.post('/api/chat', (req, res) => proxyToPython(req, res));
router.post('/api/chat/stream', (req, res) => proxyStreamToPython(req, res));

// Persistent server-side conversation history routes (backed by webapp/app/routes/conversation_history.py)
router.get('/api/conversation/history/:session_id', (req, res) => proxyToPython(req, res));
router.get('/api/conversation/context/:session_id', (req, res) => proxyToPython(req, res));
router.post('/api/conversation/clear/:session_id', (req, res) => proxyToPython(req, res));
router.get('/api/conversation/user/:user_id', (req, res) => proxyToPython(req, res));
router.post('/api/conversation/feedback/:session_id', (req, res) => proxyToPython(req, res));
router.get('/api/conversation/stats', (req, res) => proxyToPython(req, res));
router.post('/api/conversation/cleanup', (req, res) => proxyToPython(req, res));

// IFSP V2 Core routes (DuckDB-first specialist agents)
router.get('/api/v2/health', (req, res) => proxyToPython(req, res));
router.get('/api/v2/catalog', (req, res) => proxyToPython(req, res));
router.get('/api/v2/graph/items', (req, res) => proxyToPython(req, res));
router.post('/api/v2/chat', (req, res) => proxyToPython(req, res));

module.exports = router;
