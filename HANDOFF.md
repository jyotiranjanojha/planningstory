# Intel Foundry Planning Story — Project Handoff

> **Last updated:** 2026-11-26  
> **Repository:** `https://github.com/jyotiranjanojha/planningstory.git` (`origin/main` — sole canonical repository)  
> **Latest Release Tag:** `v2.1.0` (`HEAD` at `028b31b` + this handoff update)

This document captures the exact end-of-day state of the project so the next session or any team member can pick up immediately with full context.

---

## 1. Current Architecture (Single-Origin Enterprise v2.1)

| Layer | Technology | Entry Point / Location | Responsibility |
|---|---|---|---|
| **Single-Origin Gateway** | Node.js 20 + Express | `src/server.js`, `src/app.js`, `src/services/pythonProxy.js`, `src/routes/*` | Public HTTP entry (`3004` locally, `3000` in Docker/Rancher), Microsoft Entra ID OAuth2 SSO (`httpOnly` cookies + `oauth_state` CSRF guard), pooled keep-alive HTTP + unbuffered SSE proxying for `/`, `/static/*`, `/favicon.ico`, and `/api/*`, plus Kubernetes `/api/ready` probe. |
| **Internal Planning API** | Python 3.11+ / FastAPI / Uvicorn | `webapp/run.py`, `webapp/app/main.py` | Internal backend (`127.0.0.1:8001`), V2 DuckDB routes, SSE streaming, HTML report generation & SMTP email, persistent `ConversationManager` (`data/conversations.jsonl`). |
| **V2 Multi-Agent Core** | DuckDB (40 BY ESP views) + Pydantic | `webapp/app/v2/{core,agents,orchestrator,routes.py}` | Sub-second (`40–400ms`) deterministic engine with `PlanningCopilotV2` (`<1ms` intent routing, stopword-safe entity/`ITEMCLASS`/`RES_*` extraction, session follow-up memory) and **9 specialist agents**. |
| **Enterprise Web UI** | Jinja2 + Vanilla ES6 + CSS3 + SVG | `webapp/app/templates/index.html`, `webapp/app/static/{app.js,styles.css,intel-logo.svg}` | Single-row enterprise header (official Intel 2023 SVG logo, Dataset Summary, Ollama model picker, live `Connected` health pill), Gemini-style Chat Assistant, **5-Gate Visual Validation Dashboard**, Scenario Comparison, 10-mode Root Cause Analyzer, Analytics Module, and Interactive Knowledge Graph. |
| **LLM Providers** | Unified `.env`-only Client | `webapp/app/v2/core/llm.py`, `.env.example` | Default local `ollama` (`qwen2.5-coder:latest`, `gemma3:latest`) with `.env`-only switching to `nollama`, `openai`, `azure`, `anthropic`, `openvino`, or `custom`. |
| **Container & Deployment** | Docker + Harbor + Rancher (K8s) | `Dockerfile`, `docker-compose.yml`, `.github/workflows/push-to-harbor.yml`, `deploy/rancher/*` | Non-root `UID 10001` container, Intel corporate proxy defaults (`http://proxy-dmz.intel.com:912`), Harbor CI/CD, 2-replica Rancher Deployment, PVC, and SSE-enabled NGINX Ingress. |

---

## 2. All 9 Specialist V2 Agents (`webapp/app/v2/agents/`)

1. **`FulfillmentAgent`** (`fulfillment.py`) — Portfolio, item, and dynamic `ITEMCLASS` (e.g., *Stack Silicon*) fill rate, unmet/late demand, and SKU/resource drivers.
2. **`CapacityAgent`** (`capacity.py`) — Question-aware specialist supporting bottleneck overview, **resource-to-item demand impact** (`"this resource are impacting which item"` via session memory), and underutilized/spare capacity analysis.
3. **`InventoryAgent`** (`inventory.py`) — On-hand inventory, EOH projections, stockouts, and safety-stock risk.
4. **`SupplyOrdersAgent`** (`supply_orders.py`) — Make vs. Buy supply mix (`planorder`, `planpurch`, `planarriv`, `schedrcpts`).
5. **`BomLineageAgent`** (`bom_lineage.py`) — BOM explosion, where-used, alternate BOMs, and supersession chains.
6. **`ScenarioAgent`** (`scenario.py`) — Multi-scenario KPI comparison and delta ranking.
7. **`ValidationAgent`** (`validation.py`) — Powers the **5-Gate Validation Suite** including **Planning Parameters**, **Output Sanity & Solver Health**, and the new **issue-focused BOM Traversal Check** (9 structural checks: circular cycles, self-references, invalid `DRAWQTY`, duplicates, orphan items, missing SKUs at BOM loc, missing production methods, unsourceable leaf components + concrete **Flagged BOM Records** table).
8. **`KnowledgeGraphAgent`** (`graph.py`) — Real-edge supply lineage with exception health overlay and Top Unmet Items catalog (`/api/v2/graph/items`).
9. **`RootCauseAgent`** (`root_cause.py`) — Fast DuckDB analysis across all 10 planner diagnosis modes (`why_unmet`, `why_late`, `why_short`, `why_early`, `capacity_constraints`, `bom_sourcing_gaps`, `priority_conflict`, `supply_pegging`, `eoh`, `full_diagnosis`).

---

## 3. Completed Today (2026-11-26)

- **Clean Repository & New Canonical Git Repo:**
  - Removed dead/duplicate/unwired modules, untracked `.env` (kept local `.env` intact), created fresh clean history on `https://github.com/jyotiranjanojha/planningstory.git`, removed stale legacy remote, and tagged `v2.0.0` → `v2.1.0`.
  - Set `git config windows.appendAtomically false` locally to avoid OneDrive `.git/logs/HEAD` append issues.
- **UI / UX & Branding Overhaul:**
  - Replaced logo & favicon with official Intel 2023 SVG (`webapp/app/static/intel-logo.svg`).
  - Redesigned top bar into a compact single-row enterprise header (~95px more vertical workspace), widened output canvas, normalized typography (`--text-xs: 0.78rem` floor), and fixed responsive breakpoint ordering.
  - Upgraded **Validation Gate** to a visual executive dashboard (SVG Health Donut, Severity Distribution Bar, Coverage Metrics, Integrity Rules table, and **Flagged BOM Records** table — removed the giant BOM graph).
  - Upgraded **Chat Assistant** (Gemini-style UX, context-aware `CapacityAgent` + `_SESSION_LAST_RESOURCE` memory, SSE trailing-buffer flush, non-destructive Dataset Summary card).
- **Single-Origin Node Gateway & Enterprise Harbor/Rancher Readiness:**
  - Node Gateway (`src/app.js`, `src/services/pythonProxy.js`) now serves `/`, `/static/*`, `/favicon.ico`, and `/api/*` on one origin with IPv4 loopback normalization, keep-alive pooling, raw HTML/file download passthrough, `/api/ready` probe, and live frontend **Connected** indicator.
  - Hardened `Dockerfile` (non-root `UID 10001`), `start-services.sh` (readiness wait loop), `healthcheck.sh`, `.github/workflows/push-to-harbor.yml`, and created full `deploy/rancher/` Kubernetes manifests + `docs/HARBOR_RANCHER_DEPLOYMENT.md` with Intel corporate proxy defaults (`http://proxy-dmz.intel.com:912`).
- **Interactive README:** Overhauled `README.md` with full Technology Stack table, Mermaid diagrams, and GitHub-compatible SVG 1.1 diagrams (`docs/images/ui-workspace-showcase.svg`, `docs/images/architecture-overview.svg`).

---

## 4. Current Local Runtime & Verification Baseline

- **Local URLs:**
  - Primary Single-Origin UI (Node Gateway): `http://localhost:3004`
  - Direct Backend (no SSO): `http://127.0.0.1:8001`
  - Readiness Check: `GET http://127.0.0.1:3004/api/ready` → `HTTP 200` (`40` DuckDB views, `ollama / qwen2.5-coder:latest`)
- **Automated Checks Passing:**
  - `pytest -p no:cacheprovider tests/test_v2_core.py` → **10/10 passed**
  - `node --check` on all `src/**/*.js` and `webapp/app/static/app.js` → **passed**
  - YAML validation across all 10 Harbor/Rancher manifests → **10/10 valid**
  - Live snapshot data: `by_input/` (**22** CSVs) + `by_output/` (**18** CSVs)

---

## 5. Next Steps (Where to Pick Up Tomorrow)

1. **Harbor Build & Push Verification:**
   - Configure `HARBOR_USERNAME` and `HARBOR_PASSWORD` secrets in GitHub (`planningstory` repo) and trigger `.github/workflows/push-to-harbor.yml` (or run manual `docker build && docker push` per `docs/HARBOR_RANCHER_DEPLOYMENT.md`).
2. **Rancher Staging Deployment:**
   - Create `harbor-regcred` and `planningstory-secrets` in namespace `planningstory`.
   - Update `planningstory.intel.com` and `OLLAMA_BASE_URL` in `deploy/rancher/configmap.yaml` and `deploy/rancher/ingress.yaml`, then run `kubectl apply -k deploy/rancher/`.
   - Verify Entra ID SSO sign-in, `/api/ready`, SSE streaming (`/api/chat/stream`), and HTML report downloads through Rancher NGINX Ingress.
3. **Optional Next Enhancements:**
   - Connect live Snowflake PDH views / automated weekly BY ESP snapshot refresh into `PlanningDataEngine`.
   - Expand unit test coverage in `tests/test_v2_core.py` for the new `run_bom_traversal_gate()` and `CapacityAgent` resource-to-item impact mode.
