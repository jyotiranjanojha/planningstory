# Intel Foundry Planning Story — Project Handoff

> **Last updated:** 2026-11-26 (Late Evening — Shared File Path, Auto-Refresh Cache, Model Garden Custom LLM & Single-`.env` CaaS Workflow)  
> **Repository:** `https://github.com/jyotiranjanojha/planningstory.git` (`origin/main` — sole canonical repository)  
> **Latest Release Tag:** `v2.1.0` + shared-path / Model Garden / CaaS `.env` updates
> **Harbor Project:** `https://amr-registry-pre.caas.intel.com/harbor/projects/125194/repositories` (`if-pdh-ai-assist`)  
> **Rancher Namespace:** `https://amr-pre.caas.intel.com/dashboard/c/c-skrnn/explorer/namespace/if-pdh-ai-assist` (`if-pdh-ai-assist`)

This document captures the exact end-of-day state of the project so the next session or any team member can pick up immediately with full context.

---

## 1. Current Architecture (Single-Origin Enterprise v2.1)

| Layer | Technology | Entry Point / Location | Responsibility |
|---|---|---|---|
| **Single-Origin Gateway** | Node.js 20 + Express | `src/server.js`, `src/app.js`, `src/services/pythonProxy.js`, `src/routes/*` | Public HTTP entry (`3004` locally, `3000` in Docker/Rancher), Microsoft Entra ID OAuth2 SSO (`httpOnly` cookies + `oauth_state` CSRF guard), pooled keep-alive HTTP + unbuffered SSE proxying for `/`, `/static/*`, `/favicon.ico`, and `/api/*`, plus Kubernetes `/api/ready` probe. |
| **Internal Planning API** | Python 3.11+ / FastAPI / Uvicorn | `webapp/run.py`, `webapp/app/main.py` | Internal backend (`127.0.0.1:8001`), V2 DuckDB routes, SSE streaming, HTML report generation & SMTP email, persistent `ConversationManager` (`data/conversations.jsonl`). |
| **V2 Multi-Agent Core** | DuckDB (40 BY ESP views) + Pydantic | `webapp/app/v2/{core,agents,orchestrator,routes.py}` | Sub-second (`40–400ms`) deterministic engine with `PlanningCopilotV2` (`<1ms` intent routing, stopword-safe entity/`ITEMCLASS`/`RES_*` extraction, session follow-up memory) and **9 specialist agents**. |
| **Enterprise Web UI** | Jinja2 + Vanilla ES6 + CSS3 + SVG | `webapp/app/templates/index.html`, `webapp/app/static/{app.js,styles.css,intel-logo.svg}` | Single-row enterprise header (official Intel 2023 SVG logo, Dataset Summary, Ollama model picker, live `Connected` health pill), Gemini-style Chat Assistant, **5-Gate Visual Validation Dashboard**, Scenario Comparison, 10-mode Root Cause Analyzer, Analytics Module, and Interactive Knowledge Graph. |
| **LLM Providers** | Unified `.env`-only Client | `webapp/app/v2/core/llm.py`, `webapp/app/analyzer.py`, `.env.example` | Supports **`snowflake_cortex`** (both `rest` PAT/JWT mode for Rancher and `sql` `SNOWFLAKE.CORTEX.COMPLETE()` mode with Intel SSO + auto-warehouse for local testing), **`ollama`** (`qwen2.5-coder:latest`, `gemma3:latest`), `nollama`, `openai`, `azure`, `anthropic`, `openvino`, and `custom` — `.env`-only switch, zero code changes. |
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
  - Hardened `Dockerfile` (non-root `UID 10001`), `start-services.sh` (readiness wait loop), `healthcheck.sh`, `.github/workflows/push-to-harbor.yml`, and created full `deploy/rancher/` Kubernetes manifests targeting your existing **`if-pdh-ai-assist`** namespace + `docs/HARBOR_RANCHER_DEPLOYMENT.md` with Intel corporate proxy defaults (`http://proxy-dmz.intel.com:912`).
- **Snowflake Cortex Integration (REST + SQL Modes):**
  - Added `LLM_PROVIDER=snowflake_cortex` supporting both **REST** (`/api/v2/cortex/inference:complete` for Rancher containers) and **SQL** (`SNOWFLAKE.CORTEX.COMPLETE()` via `snowflake-connector-python` with cached `externalbrowser` Intel SSO and automatic warehouse selection).
  - **Verified live locally** against `XD14286-IFCDWDEV2` (`IF_WH_MDATA_CONSUMPTION`, `claude-sonnet-4-5`) — returned `Snowflake Cortex SQL Complete OK.` with zero second SSO prompt.
  - Fixed top-bar model dropdown alignment (`Cortex` compact label + flex alignment in `styles.css` / `app.js`).
- **Interactive README:** Overhauled `README.md` with full Technology Stack table, Mermaid diagrams, and GitHub-compatible SVG 1.1 diagrams (`docs/images/ui-workspace-showcase.svg`, `docs/images/architecture-overview.svg`).
- **Shared File Path + Incremental Snapshot Cache + Zero-Touch Auto-Refresh:**
  - Added centralized `resolve_data_dirs()` / `resolve_source_data_dirs()` in `webapp/app/v2/core/data.py` supporting `BY_INPUT_DIR`, `BY_OUTPUT_DIR`, and `PLANNING_DATA_DIR` (with fallback to local `by_input/` and `by_output/`), wired across `data.py`, `analyzer.py`, `langgraph_bom.py`, `text_to_sql_agent.py`, `rag.py`, and `rag_openvino.py`.
  - Added parallel incremental local snapshot cache (`PLANNING_DATA_CACHE_ENABLED=auto`, `data/snapshot_cache/`) + zero-touch background watcher (`PLANNING_DATA_REFRESH_INTERVAL_SECS=300`) that checks filename/size/`mtime`, copies only changed CSVs atomically, skips unchanged files, and reloads DuckDB views automatically (plus `POST /api/v2/reload` and full path/cache/refresh metadata in `/api/v2/health` and `/api/v2/catalog`).
  - Configured local `.env` against live Intel share `\\IFBYESPconshare-dm.cps.intel.com\IFBYESP\R3_dev\pdh_ai_app\{by_input,by_output}` (**22** input + **18** output CSVs, **36.49 MB**) and verified live end-to-end: **40** DuckDB views, **1.09s** warm startup, **26.5ms** query, **183ms** `/api/v2/chat`.
  - Added `deploy/rancher/shared-storage-smb.yaml` (`ReadOnlyMany` PV/PVC template via `smb.csi.k8s.io` + NFS alternative for CaaS).
- **Intel Model Garden (`custom` LLM) & Single-File `.env` Workflow for CaaS:**
  - Hardened `LLM_PROVIDER=custom` in `webapp/app/v2/core/llm.py` and `webapp/app/analyzer.py` for Intel Model Garden (`https://models-gcp.intel.com/v1/chat/completions`, e.g., `claude-opus-4-8`): normalizes root / `/v1` / `/v1/chat/completions` URLs and falls back cleanly to `CUSTOM_LLM_MODEL` + `CUSTOM_LLM_MODELS` if `/v1/models` is restricted.
  - Simplified `deploy/rancher/configmap.yaml` to fixed container/proxy plumbing only, added `.env.caas.example` (and `.env.*` ignore in `.gitignore`), so all app settings (LLM keys, Entra ID SSO, shared data path, refresh interval) are managed via a single `.env` locally and `.env.caas` → `planningstory-secrets` on CaaS.

---

## 4. Current Local Runtime & Verification Baseline

- **Local URLs:**
  - Primary Single-Origin UI (Node Gateway): `http://localhost:3004`
  - Direct Backend (no SSO): `http://127.0.0.1:8001`
  - Readiness Check: `GET http://127.0.0.1:3004/api/ready` → `HTTP 200` (`40` DuckDB views from live shared path cache)
- **Automated Checks Passing:**
  - `pytest -p no:cacheprovider tests/test_v2_core.py` → **14/14 passed** (includes Snowflake Cortex REST/SQL, shared-path + incremental auto-refresh cache, and Intel Model Garden `custom` LLM tests)
  - `node --check` on all `src/**/*.js` and `webapp/app/static/app.js` → **passed**
  - YAML validation across all `deploy/rancher/*.yaml` manifests (including `shared-storage-smb.yaml`) → **all valid**
  - Live shared snapshot data: `\\IFBYESPconshare-dm.cps.intel.com\IFBYESP\R3_dev\pdh_ai_app\{by_input,by_output}` (**22** input + **18** output CSVs, cached locally in `data/snapshot_cache/`)

---

## 5. Next Steps (Where to Pick Up Next)

1. **Choose Headless LLM for CaaS (Single `.env.caas` Change):**
   - **Fastest path:** Intel Model Garden (`LLM_PROVIDER=custom`, `CUSTOM_LLM_BASE_URL=https://models-gcp.intel.com`, `CUSTOM_LLM_API_KEY=sk-...`, `CUSTOM_LLM_MODEL=claude-opus-4-8`) — works identically locally and in headless CaaS pods with zero SSO friction.
   - **Alternative:** Snowflake Cortex REST (`PAT` or RSA Key-Pair JWT).
2. **Harbor Build & Push (`if-pdh-ai-assist`):**
   - Configure `HARBOR_USERNAME` and `HARBOR_PASSWORD` secrets in GitHub (`planningstory` repo) and run `.github/workflows/push-to-harbor.yml` (or run manual `docker build && docker push` to `amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory`).
3. **Rancher / CaaS Deployment into `if-pdh-ai-assist` (`c-skrnn`):**
   - Copy `.env.caas.example` → `.env.caas`, fill in LLM key + Entra ID SSO + `/mnt/planning-data`, and load into `planningstory-secrets` (`--from-env-file=.env.caas`).
   - Create `harbor-regcred` and `planningstory-smb-creds` secrets in `if-pdh-ai-assist`, apply `deploy/rancher/shared-storage-smb.yaml`, and uncomment the `planning-shared-data` mount in `deploy/rancher/deployment.yaml`.
   - Set final Ingress hostname + TLS secret in `deploy/rancher/ingress.yaml`, then run `kubectl apply -k deploy/rancher/`.
   - Verify Entra ID SSO sign-in, `/api/ready`, `/api/v2/health`, SSE streaming (`/api/chat/stream`), and HTML report downloads through Rancher NGINX Ingress.
4. **Long-Term Roadmap:**
   - Connect live Snowflake PDH tables/views as the end-state data source alongside the shared file path engine.
   - Add 5-minute `NodeCache` caching on Microsoft Graph `checkMemberGroups` in `src/app.js` (if `AZURE_REQUIRE_GROUP_CHECK=true` is enabled).
