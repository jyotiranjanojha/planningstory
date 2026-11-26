const express = require('express');
const { proxyToPython } = require('../services/pythonProxy');

const router = express.Router();

// Planning context, datasets, LLM discovery, and RAG routes
// (/api/auth/me is handled by src/app.js; validation/analysis routes by routes/analysis.js;
//  chat & conversation routes by routes/chat.js.)
router.get('/api/context/current', (req, res) => proxyToPython(req, res));
router.post('/api/context/reset', (req, res) => proxyToPython(req, res));
router.post('/api/context/resolve', (req, res) => proxyToPython(req, res));
router.get('/api/datasets/summary', (req, res) => proxyToPython(req, res));
router.get('/api/llm/models', (req, res) => proxyToPython(req, res));
router.get('/api/rag/status', (req, res) => proxyToPython(req, res));
router.post('/api/rag/reindex', (req, res) => proxyToPython(req, res));
router.post('/api/rag/query', (req, res) => proxyToPython(req, res));
router.post('/api/semantic/debug', (req, res) => proxyToPython(req, res));
router.get('/api/rag/openvino/status', (req, res) => proxyToPython(req, res));
router.post('/api/rag/openvino/export-embedding', (req, res) => proxyToPython(req, res));
router.post('/api/rag/openvino/export-reranker', (req, res) => proxyToPython(req, res));
router.post('/api/rag/openvino/reindex', (req, res) => proxyToPython(req, res));
router.post('/api/rag/openvino/query', (req, res) => proxyToPython(req, res));

module.exports = router;
