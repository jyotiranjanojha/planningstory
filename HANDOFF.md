# Intel Foundry Planning Story — Project Handoff

> **Last updated:** 2026-11-27 (Afternoon — Live Intel Model Garden `Qwen3-8B.w8a8` Verification, `LLM_TEMPERATURE=0.1`, Capacity Routing Fix & Tier-1 Dead-Code Cleanup)  
> **Repository:** `https://github.com/jyotiranjanojha/planningstory.git` (`origin/main` — sole canonical repository, latest commit `a2b1606`)  
> **Latest Release Tag:** `v2.1.0` + shared-path / Model Garden / Qwen3 / CaaS `.env` updates
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

## 3. Completed Recently

### 2026-11-27 (Latest Session — Commit `a2b1606`)
- **Live Intel Model Garden (`custom` LLM) End-to-End Verification (`Qwen3-8B.w8a8`):**
  - Configured local `.env` with `LLM_PROVIDER=custom`, `CUSTOM_LLM_BASE_URL=https://models-gcp.intel.com`, `CUSTOM_LLM_MODEL=Qwen3-8B.w8a8`, and live API key (`.env` remains gitignored).
  - Caught and fixed Qwen3 `<think>...</think>` reasoning token leakage/truncation: added `should_disable_thinking()` (automatically sends `chat_template_kwargs: {"enable_thinking": False}` for Qwen3 on `custom` endpoints, overridable via `CUSTOM_LLM_DISABLE_THINKING`) and `strip_think_tags()` in `webapp/app/v2/core/llm.py` and `webapp/app/analyzer.py`.
  - Verified live: `UnifiedLLMClient.chat()` → `'Model Garden OK'`, `UnifiedLLMClient.stream()` → `7` SSE chunks (`'1, 2, 3'`), `GET /api/ready` → `HTTP 200` (`40` DuckDB views, `custom` / `Qwen3-8B.w8a8`).
- **Deterministic Temperature (`LLM_TEMPERATURE=0.1`):**
  - Added `.env`-driven `LLM_TEMPERATURE` (default `0.1`, clamped `[0.0, 2.0]`) across `webapp/app/v2/core/llm.py`, `webapp/app/analyzer.py`, `.env`, `.env.example`, and `.env.caas.example`.
- **Browser Log Audit & `CapacityAgent` Routing / Metrics Fix:**
  - Fixed a routing bug in `webapp/app/v2/orchestrator/copilot.py` where bare `" vs "` / `"versus"` in `_SCENARIO_KEYWORDS` hijacked `"What is the overall capacity utilization vs capacity available"` into `ScenarioAgent`. Replaced with explicit scenario phrases (`scenario vs`, `week vs`, `constrained vs`, `unconstrained vs`).
  - Updated `webapp/app/v2/agents/capacity.py` to surface **Total Capacity Load vs Available** (`total_load`, `total_avail_cap`, `weighted_utilization_pct`) directly in findings and metrics.
- **Root Cause Module — Business 3-Pillar Executive View:**
  - Upgraded `RootCauseAgent` (`webapp/app/v2/agents/root_cause.py`) to emit a structured `executive_summary` + 3-section narrative answering **1. What is the Problem**, **2. Why the Problem Happened**, and **3. Possible Options to Fix the Problem** (evidence-driven action cards with Owner + Impact badges).
  - Upgraded `renderRootCauseOutput()` (`webapp/app/static/app.js`, `webapp/app/static/styles.css`) to render color-coded 3-pillar executive cards directly below the KPI stats strip.
- **Tier-1 Dead-Code Cleanup (`-1,466` lines):**
  - Removed 10 unreferenced files: unwired OpenVINO document RAG cluster (`document_ingest_pipeline.py`, `document_loader.py`, `chunking.py`, `embedding_service.py`, `vector_store.py`, `retriever.py`, `tests/test_hybrid_rag_components.py`), orphan `kpi_engine.py` + `tests/test_kpi_engine.py`, and `scripts/verify_chat_context_fix.py`.
  - Kept `rag.py`, `text_to_sql_agent.py`, `rag_openvino.py`, and `semantic_regression.py` intact (for BY ESP schema-drift flexibility; Tier-2 cleanup deferred).

### 2026-11-26

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
  - `pytest -p no:cacheprovider tests/test_v2_core.py` → **17/17 passed** (includes Snowflake Cortex REST/SQL, shared-path + incremental auto-refresh cache, Intel Model Garden `custom` LLM, Qwen3 `<think>` stripping + `LLM_TEMPERATURE=0.1`, `CapacityAgent` routing, and 3-pillar `RootCauseAgent` executive summary tests)
  - `webapp.app.main` import check → **passed** (`MAIN_IMPORT_OK`)
  - `node --check` on all `src/**/*.js` and `webapp/app/static/app.js` → **passed**
  - YAML validation across all `deploy/rancher/*.yaml` manifests (including `shared-storage-smb.yaml`) → **all valid**
  - Live shared snapshot data: `\\IFBYESPconshare-dm.cps.intel.com\IFBYESP\R3_dev\pdh_ai_app\{by_input,by_output}` (**22** input + **18** output CSVs, cached locally in `data/snapshot_cache/`)
  - Active local LLM: `LLM_PROVIDER=custom`, `CUSTOM_LLM_MODEL=Qwen3-8B.w8a8`, `LLM_TEMPERATURE=0.1` (`https://models-gcp.intel.com`)

---

## 5. Next Steps (Where to Pick Up Next)

1. **Optional Refinement Before CaaS Rollout:**
   - Filter out sentinel/infinite `AVAILCAP` rows in `CapacityAgent` weighted utilization ratio if desired (currently `total_avail_cap` includes very large unconstrained resource capacities).
   - Optionally add BY ESP schema-contract validation checks to `ValidationAgent` / V2 golden-query tests to guard against future BY file structure changes.
2. **Harbor Build & Push (`if-pdh-ai-assist`):**
   - Configure `HARBOR_USERNAME` and `HARBOR_PASSWORD` secrets in GitHub (`planningstory` repo) and run `.github/workflows/push-to-harbor.yml` (or run manual `docker build && docker push` to `amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory`).
3. **Rancher / CaaS Deployment into `if-pdh-ai-assist` (`c-skrnn`):**
   - Copy `.env.caas.example` → `.env.caas`, fill in verified Model Garden settings (`LLM_PROVIDER=custom`, `LLM_TEMPERATURE=0.1`, `CUSTOM_LLM_BASE_URL=https://models-gcp.intel.com`, `CUSTOM_LLM_API_KEY=sk-...`, `CUSTOM_LLM_MODEL=Qwen3-8B.w8a8` or `claude-opus-4-8`) + Entra ID SSO + `/mnt/planning-data`, and load into `planningstory-secrets` (`--from-env-file=.env.caas`).
   - Create `harbor-regcred` and `planningstory-smb-creds` secrets in `if-pdh-ai-assist`, apply `deploy/rancher/shared-storage-smb.yaml`, and uncomment the `planning-shared-data` mount in `deploy/rancher/deployment.yaml`.
   - Set final Ingress hostname + TLS secret in `deploy/rancher/ingress.yaml`, then run `kubectl apply -k deploy/rancher/`.
   - Verify Entra ID SSO sign-in, `/api/ready`, `/api/v2/health`, SSE streaming (`/api/chat/stream`), and HTML report downloads through Rancher NGINX Ingress.
4. **Long-Term Roadmap:**
   - Connect live Snowflake PDH tables/views as the end-state data source alongside the shared file path engine.
   - Add Model Garden `/v1/embeddings`-backed document RAG for unstructured BY ESP data dictionaries / planner SOPs (replacing legacy OpenVINO RAG).
   - Add 5-minute `NodeCache` caching on Microsoft Graph `checkMemberGroups` in `src/app.js` (if `AZURE_REQUIRE_GROUP_CHECK=true` is enabled).
