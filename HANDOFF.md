# IFSP Story — Project Handoff

> **Last updated:** 2026-11-26  
> **Repository:** `ifspstory` (Intel Foundry Supply Planning AI Assistant)  
> **Git HEAD:** `abe3fb3` (`Fix chat assistant context retention, proxy streaming, and intent routing bugs`)

This document captures the current state of the project after reviewing `README.md`, `docs/PRD.md`, architecture/readiness docs, recent git history, and the codebase so any contributor or session can pick up immediately where work left off.

---

## 1. Project Purpose & Business Goal

From `docs/PRD.md` and `README.md`:

- **Problem:** Intel Foundry Supply Planning, Planning Data Hub (PDH), and Blue Yonder ESP (BY ESP) teams spend significant manual effort joining input and output snapshots to explain *why demand is met or unmet*, trace BOM/supply lineage, validate master data/parameters, and compare weekly scenarios.
- **Objective:** Build an end-to-end **Agentic Hybrid RAG & Explainability Assistant** over BY ESP planning data (`by_input` and `by_output`, CSV snapshots or Snowflake PDH) that:
  - Validates input master data, BOMs, parameters, and solver outputs.
  - Answers functional, KPI, root-cause, and scenario-comparison questions with deterministic evidence and citations.
  - Supports multi-scenario weekly runs and continuous self-learning over new weekly datasets.
  - Enforces grounded, evidence-first answers (no fabricated data; explicit separation of confirmed facts vs. hypotheses).

---

## 2. Current Architecture

The application runs as a **Hybrid Node.js + Python** service:

| Layer | Technology | Entry Point / Location | Responsibility |
|---|---|---|---|
| **Public Front Door** | Node.js + Express | `src/server.js`, `src/app.js`, `src/services/pythonProxy.js` | Public HTTP entry (port `3004` locally via `start-ifsp.bat`, `3000` in Docker), Microsoft Entra ID (Azure AD) OAuth2 SSO (`/login`, `/auth/callback`, `/api/auth/me`), and HTTP + unbuffered SSE proxying to Python. |
| **Planning Engine** | Python 3.11+ / FastAPI / Uvicorn | `webapp/run.py`, `webapp/app/main.py` | Core planning APIs (internal port `8001` locally/Docker), deterministic workflows, SSE streaming with GPU semaphore, optional API auth & rate limiting. |
| **Web UI** | Jinja2 HTML + Vanilla JS/CSS | `webapp/app/templates/index.html`, `webapp/app/static/app.js`, `webapp/app/static/styles.css` | Gemini-style Chat Assistant, 5-Gate Validation Suite (Data Quality, Planning Parameters, BOM Traversal, Production Route, Output Sanity & Solver Health), Scenario Comparison, Root Cause V2, Insights Trends, and interactive Knowledge Graph. |
| **Orchestration & Routing** | V2 Copilot + Legacy Routers | `webapp/app/v2/orchestrator/copilot.py`, `webapp/app/analyzer.py`, `webapp/app/router_agent.py` | Deterministic-first intent routing, stopword-safe entity & `ITEMCLASS` extraction, session follow-up memory, and grounded prompt construction. |
| **Deterministic Engines** | DuckDB / Pandas / NumPy / LangGraph | `webapp/app/v2/core/data.py`, `webapp/app/kpi_engine.py`, `webapp/app/grounding_engine.py`, `webapp/app/langgraph_bom.py`, `webapp/app/text_to_sql_agent.py`, `webapp/app/sql_backends.py` | 40-view DuckDB BY ESP catalog, KPI calculation, BOM traversal, safe Text-to-SQL (`SELECT`-only + `LIMIT`), DuckDB (active) & Snowflake (abstracted) backends. |
| **RAG & Embeddings** | FAISS + OpenVINO | `webapp/app/rag.py`, `webapp/app/rag_openvino.py`, `webapp/app/vector_store.py`, `webapp/app/embedding_service.py`, `webapp/app/retriever.py`, `webapp/app/chunking.py` | Document & CSV indexing, dense retrieval, optional OpenVINO embedding/reranker models. |
| **Context & History** | JSONL + JSON Index | `webapp/app/services/conversation_manager.py`, `webapp/app/context_resolution.py`, `webapp/app/routes/conversation_history.py` | Persistent server-side multi-turn conversation history (`data/conversations.jsonl`, `data/sessions_index.json`) with UTC timestamps, atomic index writes, and pronoun/entity resolution. |
| **V2 Core Engine** | DuckDB + Pydantic + FastAPI | `webapp/app/v2/{core,agents,orchestrator,routes.py}` | DuckDB-first 40-view BY ESP engine, unified `.env`-only LLM client, and 9 specialist agents (Fulfillment, Inventory, Capacity, Supply Orders, BOM Lineage, Scenario, Validation, Knowledge Graph, Root Cause) powering `/api/v2/*`, `/api/chat/stream`, `/api/knowledge-graph`, `/api/validate`, and `/api/root-cause`. |
| **LLM Providers** | Multi-Provider | Configured via `.env` (`LLM_PROVIDER`) | Supports `ollama` (default local), `nollama`, `openai`, `azure`, `anthropic`, `openvino`, `custom`, plus `NO_LLM_RESPONSE_MODE` deterministic mode. |
| **CI / Quality Gates** | GitHub Actions + pytest | `.github/workflows/semantic-regression.yml`, `.github/workflows/push-to-harbor.yml`, `tests/semantic/run_semantic_regression.py` | Semantic regression gates (Intent >= 95%, File Mapping >= 95%, KPI >= 90%, Hallucination = 0%) and Harbor container push. |

---

## 3. Work Completed Recently

### Latest Commits
- **`abe3fb3` — Fix chat assistant context retention, proxy streaming, and intent routing bugs** (30 files, +8,263 lines):
  - Added persistent server-side `ConversationManager` (`webapp/app/services/conversation_manager.py`) and 6 endpoints under `/api/conversation/*` (`webapp/app/routes/conversation_history.py`), wired into `webapp/app/context_resolution.py` and `webapp/app/main.py`.
  - Added unbuffered `proxyStreamToPython()` in `src/services/pythonProxy.js` for `/api/chat/stream` SSE streaming.
  - Fixed Windows `cmd.exe` trailing-whitespace bug in `start-ifsp.bat` and added defensive `.trim()` on `PYTHON_BASE_URL` in `src/services/pythonProxy.js`.
  - Added missing deterministic workflow formatters in `webapp/app/analyzer.py` (Analytics Insights, Domain Focus Fulfillment/Generation/Data Hygiene, plus catch-all).
  - Fixed intent routing tie-breaks and classified on the user's original question text rather than context-expanded text (`webapp/app/router_agent.py`).
  - Fixed late-supply root cause follow-ups so questions like *"why N units are late"* return late-supply breakdowns instead of generic summaries.
  - Implemented all 5 layers of the Self-Learning system + `learning_analytics.py` routes + comprehensive docs (`docs/AGENT_SELF_LEARNING_STRATEGY.md`, `docs/SELF_LEARNING_IMPLEMENTATION_GUIDE.md`, `docs/SELF_LEARNING_IMPLEMENTATION_SUMMARY.md`).
  - Added verification script `verify_chat_context_fix.py`.
- **`5d983b3` — Harden chat streaming and focused follow-ups**
- **`0d7c5b3` — Harden Windows app lifecycle scripts** (`start-ifsp.bat`, `stop-ifsp.bat`)
- **`99371e5` — Stabilize Entra sign-in and planning UI**
- **`11826d6` — Add Node + Python hybrid runtime and deployment setup** (`Dockerfile`, `docker-compose.yml`, `start-services.sh`)

---

## 4. Current Workspace State

- **Git status:**
  - `M .env` (15 local lines added — keep secrets local, do not commit)
  - `?? data/` (`conversations.jsonl` ~10.5 KB, `sessions_index.json` ~227 B — runtime conversation store generated by `ConversationManager`)
- **Snapshot data present locally:**
  - `by_input/`: **22** CSV files
  - `by_output/`: **18** CSV files
- **Documented test baseline (`README.md`):** 65 passed, 2 skipped (`pytest tests -q`). *(Note: pytest was not run to completion during this quick scan; run locally before starting feature edits.)*

---

## 5. Open Gaps & Next Workstreams (Where to Pick Up)

These items were identified directly from the code scan and architecture/readiness docs:

### Priority 1 — Completed Clean-Code & V2 Upgrades
- Built modular **V2 Core (`webapp/app/v2/`)** with 40-view DuckDB engine and 9 specialist agents (`FulfillmentAgent`, `InventoryAgent`, `CapacityAgent`, `SupplyOrderAgent`, `BomLineageAgent`, `ScenarioAgent`, `ValidationAgent`, `KnowledgeGraphAgent`, `RootCauseAgent`).
- Upgraded **Chat Assistant** to Gemini-style streaming UX (welcome screen, history hydration, true New Chat reset, Stop/Copy/Regenerate, follow-up chips, collapsible filters).
- Upgraded **Knowledge Graph** (Top Unmet quick-picks, searchable item datalist, KPI strip, curved Bezier lineage, health overlay, Bottlenecks-Only toggle, Zoom, drill-through).
- Upgraded **Validation Gate** to 5 gates (Data Quality, Planning Parameters, BOM Traversal, Production Route, Output Sanity & Solver Health) with HTML export & email on all gates.
- Upgraded **Root Cause** to DuckDB V2 (`<500ms` across all 10 question types, quick-picks, drill-through).
- Hardened Entra SSO (`oauth_state` cookie validation, no plaintext token logs, no `access_token` in redirect URL), switched default local LLM provider to `ollama`, untracked `.env`, and removed dead/unwired files (`src/routes/api.js`, unwired self-learning stubs, scratch tests).

### Priority 2 — Production Hardening (from `docs/ARCHITECTURE_REVIEW.md`, `docs/PRODUCTION_READINESS_CHECKLIST.md`, `docs/RISK_REGISTER.md`)
- **Security (R-01, R-10, R-15):** Enable and harden `API_AUTH_ENABLED` / RBAC on state-mutating and expensive endpoints (`/api/rag/reindex`, exports, email), enable `RATE_LIMIT_ENABLED`, add prompt/body size caps and log redaction.
- **Snowflake Hardening (R-04, R-11):** Add Snowflake query tags (`intent`, `workflow`, `user`, `trace_id`), approved view/schema allowlist, pushdown optimization, and cost/latency telemetry.
- **OpenVINO & Performance (R-05, R-06, R-07):** Startup warmup for OpenVINO LLM/embeddings, device benchmark matrix (CPU/NPU/GPU on Intel Ultra 7), bounded LRU/TTL caches with memory guardrails, and per-route token caps.
- **Hallucination & Citation Gating (R-02, R-09):** Enforce mandatory citation & confidence/missing-evidence sections across SQL+RAG hybrid answers; propagate `trace_id` across LangGraph nodes.

### Priority 3 — PRD Roadmap Alignment (`docs/PRD.md`)
- **Teams-based Planning Copilot:** Deployment guide exists (`docs/TEAMS_AGENT_DEPLOYMENT.md`); integrate/deploy when ready.
- **Weekly Automated Refresh:** Connect Snowflake live views (or scheduled CSV drop ingestion) with `DataDriftDetector` and `SchemaRegistry` once the self-learning system is wired.

---

## 6. Quick Start & Verification Commands

### Run Locally (Windows)
```bat
:: Start Python backend (8001) + Node front door (3004)
start-ifsp.bat

:: Stop both services
stop-ifsp.bat
```
- Node UI / Entra Login: `http://localhost:3004/login`
- Python Backend (internal): `http://localhost:8001`

### Run via Docker Compose
```bash
docker compose up --build
```
*(Exposes Node on `3000` and Python on `8001`.)*

### Verification & Tests
```bash
# Full pytest suite
.venv\Scripts\python.exe -m pytest tests -q

# Chat context retention verification
.venv\Scripts\python.exe scripts/verify_chat_context_fix.py

# Semantic regression quality gate
.venv\Scripts\python.exe tests/semantic/run_semantic_regression.py
```

---

## 7. Key Files Reference

| Area | Files |
|---|---|
| **Product & Architecture Docs** | `README.md`, `docs/PRD.md`, `docs/ARCHITECTURE_REVIEW.md`, `docs/PRODUCTION_READINESS_CHECKLIST.md`, `docs/RISK_REGISTER.md`, `docs/OPERATIONS_RUNBOOK.md` |
| **Chat Context Docs** | `docs/CHAT_CONTEXT_RETENTION_FIX.md` |
| **Self-Learning Strategy** | `docs/AGENT_SELF_LEARNING_STRATEGY.md` |
| **Node Front Door** | `src/server.js`, `src/app.js`, `src/config/env.js`, `src/services/pythonProxy.js`, `src/routes/{health,planning,analysis,chat}.js` |
| **Python Backend Core** | `webapp/run.py`, `webapp/app/main.py`, `webapp/app/analyzer.py`, `webapp/app/router_agent.py`, `webapp/app/context_resolution.py` |
| **SQL, BOM, KPI, RAG** | `webapp/app/text_to_sql_agent.py`, `webapp/app/sql_backends.py`, `webapp/app/langgraph_bom.py`, `webapp/app/kpi_engine.py`, `webapp/app/grounding_engine.py`, `webapp/app/rag.py`, `webapp/app/rag_openvino.py` |
| **Conversation & Learning** | `webapp/app/services/conversation_manager.py`, `webapp/app/routes/conversation_history.py`, `webapp/app/services/learning_system.py`, `webapp/app/routes/learning_analytics.py` |
| **Frontend UI** | `webapp/app/templates/index.html`, `webapp/app/static/app.js`, `webapp/app/static/styles.css` |
| **Agent & Prompt Specs** | `.github/agents/*.agent.md`, `.github/prompts/*.prompt.md` |
