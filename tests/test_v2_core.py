"""Tests for IFSP V2 Core (DuckDB data engine, specialist agents, copilot)."""

from pathlib import Path

from webapp.app.v2.agents.graph import KnowledgeGraphAgent
from webapp.app.v2.agents.root_cause import RootCauseAgent
from webapp.app.v2.core.data import get_data_engine
from webapp.app.v2.core.llm import load_llm_settings
from webapp.app.v2.orchestrator.copilot import PlanningCopilotV2, extract_item_id

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_v2_duckdb_catalog_loads_snapshots():
    engine = get_data_engine(REPO_ROOT)
    summary = engine.catalog_summary()
    assert summary["total_views"] >= 10
    assert engine.has_table("by_if_snop_out_inddmdview")


def test_v2_stopword_safe_item_extraction():
    assert extract_item_id("check if demand for all order point item are meet or not") is None
    assert extract_item_id("why 2232 unmet") is None
    assert extract_item_id("why is demand unmet for item 100000000004?") == "100000000004"


def test_v2_fulfillment_portfolio_and_followup():
    copilot = PlanningCopilotV2(REPO_ROOT)

    res1 = copilot.handle("check if demand for all order point item are meet or not")
    assert res1 is not None
    assert res1.agent == "FulfillmentAgent"
    assert res1.metrics["total_demand"] > 0
    assert res1.duration_ms < 5000

    res2 = copilot.handle("why 2232 unmet")
    assert res2 is not None
    assert res2.agent == "FulfillmentAgent"
    assert len(res2.root_causes) > 0 or len(res2.rows) > 0
    assert res2.duration_ms < 5000

    res3 = copilot.handle("explain why demand unmet for Stack Silicon items")
    assert res3 is not None
    assert res3.agent == "FulfillmentAgent"
    assert "Stack Silicon" in res3.summary
    assert res3.summary != res1.summary


def test_v2_validation_and_scenario_agents():
    copilot = PlanningCopilotV2(REPO_ROOT)

    val = copilot.handle("run master data validation check")
    assert val is not None
    assert val.agent == "ValidationAgent"
    assert val.metrics["checks_executed"] >= 1

    # Verify new V2 Planning Parameters & Output Sanity validation gates
    pp = copilot.validation.run_planning_parameters_gate()
    assert pp["Readiness Verdict (Pass, Conditional Pass, Fail)"] in {"Pass", "Conditional Pass", "Fail"}
    assert len(pp["Checks Executed"]) >= 1

    os_gate = copilot.validation.run_output_sanity_gate()
    assert os_gate["Readiness Verdict (Pass, Conditional Pass, Fail)"] in {"Pass", "Conditional Pass", "Fail"}
    assert "fill_rate_pct" in os_gate["Checks Executed"]

    sc = copilot.handle("compare scenario performance")
    assert sc is not None
    assert sc.agent == "ScenarioAgent"
    assert sc.metrics["scenarios_found"] >= 1


def test_v2_knowledge_graph_real_lineage_and_items():
    agent = KnowledgeGraphAgent(get_data_engine(REPO_ROOT))
    items = agent.list_selectable_items(limit=10)
    assert len(items) > 0
    top_item = items[0]["item_id"]

    g = agent.build_graph(item_id=top_item)
    assert g["Graph Summary"]["demand_qty_total"] > 0
    assert len(g["nodes"]) >= 1
    assert g["Graph Summary"]["duration_ms"] < 5000


def test_v2_supply_orders_and_bom_lineage_agents():
    copilot = PlanningCopilotV2(REPO_ROOT)

    sup = copilot.handle("Show planned orders and make vs buy supply mix")
    assert sup is not None and sup.agent == "SupplyOrdersAgent"
    assert sup.metrics["total_supply_qty"] >= 0

    bom_all = copilot.handle("Show BOM structure and alternate BOM summary")
    assert bom_all is not None and bom_all.agent == "BomLineageAgent"
    assert bom_all.metrics["total_bom_links"] > 0

    bom_cls = copilot.handle("Show BOM lineage for Stack Silicon items")
    assert bom_cls is not None and bom_cls.agent == "BomLineageAgent"
    assert "Stack Silicon" in bom_cls.summary


def test_v2_inventory_and_capacity_agents_and_topic_followup():
    copilot = PlanningCopilotV2(REPO_ROOT)

    inv = copilot.handle("How does the Inventory position look like", session_id="inv-test-1")
    assert inv is not None and inv.agent == "InventoryAgent"
    assert inv.metrics["total_on_hand"] >= 0

    # Follow-up stays on InventoryAgent via session topic memory
    inv_followup = copilot.handle("show me across all item", session_id="inv-test-1")
    assert inv_followup is not None and inv_followup.agent == "InventoryAgent"

    cap = copilot.handle("Which resources have capacity bottlenecks?", session_id="cap-test-1")
    assert cap is not None and cap.agent == "CapacityAgent"
    assert cap.metrics["distinct_resources"] >= 0


def test_v2_greeting_and_dataset_intents():
    copilot = PlanningCopilotV2(REPO_ROOT)
    hi = copilot.handle("hi")
    assert hi is not None and hi.workflow == "Assistant Guide"
    ds = copilot.handle("what datasets are loaded?")
    assert ds is not None and ds.workflow == "Dataset Inventory" and ds.metrics["total_views"] >= 10


def test_v2_root_cause_agent_all_modes():
    agent = RootCauseAgent(get_data_engine(REPO_ROOT))
    for qtype in ("full_diagnosis", "why_unmet", "why_late", "eoh", "bom_sourcing_gaps", "supply_pegging"):
        res = agent.analyze(demand_id="2000-280-868", question_type=qtype)
        assert res["stats"]["demand_qty"] > 0
        assert res["stats"]["duration_ms"] < 5000
        assert res["narrative"]


def test_v2_llm_settings_loopback_normalization(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    settings = load_llm_settings()
    assert settings.provider == "ollama"
    assert settings.base_url == "http://127.0.0.1:11434"


def test_v2_snowflake_cortex_llm_provider(monkeypatch):
    from webapp.app.v2.core.llm import UnifiedLLMClient

    monkeypatch.setenv("LLM_PROVIDER", "snowflake_cortex")
    monkeypatch.setenv("SNOWFLAKE_CORTEX_ACCOUNT", "intel-foundry-prod")
    monkeypatch.setenv("SNOWFLAKE_CORTEX_TOKEN", "test-pat-token")
    monkeypatch.setenv("SNOWFLAKE_CORTEX_MODEL", "claude-3-5-sonnet")

    settings = load_llm_settings()
    assert settings.provider == "snowflake_cortex"
    assert settings.base_url == "https://intel-foundry-prod.snowflakecomputing.com"
    assert settings.model == "claude-3-5-sonnet"

    client = UnifiedLLMClient(settings)
    assert client._completions_url() == "https://intel-foundry-prod.snowflakecomputing.com/api/v2/cortex/inference:complete"
    headers = client._headers()
    assert headers["Authorization"] == "Bearer test-pat-token"
    assert headers["X-Snowflake-Authorization-Token-Type"] == "PROGRAMMATIC_ACCESS_TOKEN"

    listed = client.list_models()
    assert listed["provider"] == "snowflake_cortex"
    assert listed["reachable"] is True
    assert "claude-3-5-sonnet" in listed["models"]


def test_v2_snowflake_cortex_sql_mode(monkeypatch):
    from webapp.app.v2.core.llm import _parse_cortex_sql_result

    monkeypatch.setenv("LLM_PROVIDER", "snowflake_cortex")
    monkeypatch.setenv("SNOWFLAKE_CORTEX_MODE", "sql")
    monkeypatch.setenv("SNOWFLAKE_CORTEX_ACCOUNT", "XD14286-IFCDWDEV2")
    monkeypatch.setenv("SNOWFLAKE_USER", "planner@intel.com")

    settings = load_llm_settings()
    assert settings.provider == "snowflake_cortex"
    assert settings.cortex_mode == "sql"
    assert settings.base_url == "https://XD14286-IFCDWDEV2.snowflakecomputing.com"

    assert _parse_cortex_sql_result('{"choices":[{"messages":"Cortex SQL OK"}]}') == "Cortex SQL OK"
    assert _parse_cortex_sql_result('{"choices":[{"message":{"content":"Nested OK"}}]}') == "Nested OK"
    assert _parse_cortex_sql_result("Plain text reply") == "Plain text reply"


def test_v2_shared_data_path_overrides(monkeypatch, tmp_path):
    from webapp.app.v2.core.data import resolve_data_dirs

    shared_root = tmp_path / "shared_share"
    (shared_root / "by_input").mkdir(parents=True)
    (shared_root / "by_output").mkdir(parents=True)
    (shared_root / "by_input" / "if_snop_item-20260101000000.csv").write_text(
        "ITEM|ITEMCLASS\nITEM_SHARED_1|Stack Silicon\n", encoding="utf-8"
    )
    (shared_root / "by_output" / "by_if_snop_out_inddmdview-20260101000000.csv").write_text(
        "ITEM|LOC|QTY|SCHEDQTY\nITEM_SHARED_1|LOC1|100|80\n", encoding="utf-8"
    )

    # 1. PLANNING_DATA_DIR overrides both by_input and by_output
    monkeypatch.setenv("PLANNING_DATA_DIR", str(shared_root))
    monkeypatch.delenv("BY_INPUT_DIR", raising=False)
    monkeypatch.delenv("BY_OUTPUT_DIR", raising=False)
    in_dir, out_dir = resolve_data_dirs(REPO_ROOT)
    assert in_dir == shared_root / "by_input"
    assert out_dir == shared_root / "by_output"

    engine = get_data_engine(REPO_ROOT)
    summary = engine.catalog_summary()
    assert summary["input_dir"] == str(shared_root / "by_input")
    assert summary["output_dir"] == str(shared_root / "by_output")
    assert summary["total_views"] == 2
    rows = engine.query('SELECT ITEM, QTY FROM "by_if_snop_out_inddmdview"')
    assert rows == [{"ITEM": "ITEM_SHARED_1", "QTY": "100"}]

    # 2. Explicit BY_INPUT_DIR / BY_OUTPUT_DIR take precedence over PLANNING_DATA_DIR
    custom_in = tmp_path / "custom_in"
    custom_out = tmp_path / "custom_out"
    custom_in.mkdir()
    custom_out.mkdir()
    monkeypatch.setenv("BY_INPUT_DIR", str(custom_in))
    monkeypatch.setenv("BY_OUTPUT_DIR", str(custom_out))
    in_dir2, out_dir2 = resolve_data_dirs(REPO_ROOT)
    assert in_dir2 == custom_in
    assert out_dir2 == custom_out

    # 3. Incremental local cache sync mirrors CSVs and reloads cleanly
    cache_dir = tmp_path / "local_cache"
    monkeypatch.setenv("PLANNING_DATA_DIR", str(shared_root))
    monkeypatch.delenv("BY_INPUT_DIR", raising=False)
    monkeypatch.delenv("BY_OUTPUT_DIR", raising=False)
    monkeypatch.setenv("PLANNING_DATA_CACHE_ENABLED", "true")
    monkeypatch.setenv("PLANNING_DATA_CACHE_DIR", str(cache_dir))

    cached_engine = get_data_engine(REPO_ROOT)
    cat = cached_engine.catalog_summary()
    assert cat["cache_enabled"] is True
    assert cat["input_dir"] == str(cache_dir / "by_input")
    assert cat["source_input_dir"] == str(shared_root / "by_input")
    assert cat["total_views"] == 2
    assert (cache_dir / "by_input" / "if_snop_item-20260101000000.csv").exists()

    # Add a new file on the shared source and trigger zero-touch auto-detection check
    check_no_change = cached_engine.check_for_updates()
    assert check_no_change["changed"] is False
    assert check_no_change["copied"] == 0
    assert check_no_change["skipped"] == 2

    (shared_root / "by_input" / "if_snop_loc-20260101000000.csv").write_text(
        "LOC|DESCR\nLOC1|Fab1\n", encoding="utf-8"
    )
    check_added = cached_engine.check_for_updates()
    assert check_added["changed"] is True
    assert check_added["copied"] == 1
    assert check_added["skipped"] == 2
    assert check_added["total_views"] == 3
    assert cached_engine.has_table("if_snop_loc")

    # Modify an existing output file (size change) and verify latest data is picked up
    (shared_root / "by_output" / "by_if_snop_out_inddmdview-20260101000000.csv").write_text(
        "ITEM|LOC|QTY|SCHEDQTY\nITEM_SHARED_1|LOC1|250|200\nITEM_SHARED_2|LOC1|50|50\n",
        encoding="utf-8",
    )
    check_modified = cached_engine.check_for_updates()
    assert check_modified["changed"] is True
    assert check_modified["copied"] == 1
    assert check_modified["skipped"] == 2
    rows_updated = cached_engine.query('SELECT ITEM, QTY FROM "by_if_snop_out_inddmdview" ORDER BY ITEM')
    assert rows_updated == [
        {"ITEM": "ITEM_SHARED_1", "QTY": "250"},
        {"ITEM": "ITEM_SHARED_2", "QTY": "50"},
    ]


def test_v2_custom_llm_model_garden(monkeypatch):
    from webapp.app.v2.core.llm import UnifiedLLMClient

    for raw_url in (
        "https://models-gcp.intel.com",
        "https://models-gcp.intel.com/v1",
        "https://models-gcp.intel.com/v1/chat/completions",
    ):
        monkeypatch.setenv("LLM_PROVIDER", "custom")
        monkeypatch.setenv("CUSTOM_LLM_BASE_URL", raw_url)
        monkeypatch.setenv("CUSTOM_LLM_API_KEY", "sk-test-model-garden")
        monkeypatch.setenv("CUSTOM_LLM_MODEL", "claude-opus-4-8")

        settings = load_llm_settings()
        assert settings.provider == "custom"
        assert settings.base_url == "https://models-gcp.intel.com"
        assert settings.model == "claude-opus-4-8"

        client = UnifiedLLMClient(settings)
        assert client._completions_url() == "https://models-gcp.intel.com/v1/chat/completions"
        headers = client._headers()
        assert headers["Authorization"] == "Bearer sk-test-model-garden"
        assert headers["Content-Type"] == "application/json"


def test_v2_qwen3_think_stripping_and_toggle(monkeypatch):
    from webapp.app.v2.core.llm import should_disable_thinking, strip_think_tags

    assert strip_think_tags("<think>\ninternal reasoning\n</think>\n\nModel Garden OK") == "Model Garden OK"
    assert strip_think_tags("reasoning</think>Final Answer") == "Final Answer"
    assert strip_think_tags("<think>truncated reasoning") == ""
    assert strip_think_tags("Plain response") == "Plain response"

    monkeypatch.delenv("CUSTOM_LLM_DISABLE_THINKING", raising=False)
    assert should_disable_thinking("custom", "Qwen3-8B.w8a8") is True
    assert should_disable_thinking("custom", "claude-opus-4-8") is False

    monkeypatch.setenv("CUSTOM_LLM_DISABLE_THINKING", "false")
    assert should_disable_thinking("custom", "Qwen3-8B.w8a8") is False
    monkeypatch.setenv("CUSTOM_LLM_DISABLE_THINKING", "true")
    assert should_disable_thinking("custom", "claude-opus-4-8") is True

    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://models-gcp.intel.com")
    monkeypatch.setenv("CUSTOM_LLM_MODEL", "Qwen3-8B.w8a8")
    assert load_llm_settings().temperature == 0.1
    monkeypatch.setenv("LLM_TEMPERATURE", "0.0")
    assert load_llm_settings().temperature == 0.0


def test_v2_capacity_vs_available_routing():
    copilot = PlanningCopilotV2(Path(__file__).resolve().parents[1])
    assert copilot.classify_route("What is the overall capacity utilization vs capacity available") == "capacity"
    assert copilot.classify_route("Compare scenario CONSTRAINED vs UNCONSTRAINED") == "scenario"


def test_v2_root_cause_executive_summary():
    from webapp.app.v2.agents.root_cause import RootCauseAgent

    engine = get_data_engine(Path(__file__).resolve().parents[1])
    result = RootCauseAgent(engine).analyze(demand_id="2000-280-868", question_type="full_diagnosis")
    exec_sum = result.get("executive_summary") or {}
    assert "what_is_the_problem" in exec_sum
    assert "why_it_happened" in exec_sum
    assert "possible_options_to_fix" in exec_sum
    assert exec_sum["what_is_the_problem"]["headline"]
    assert len(exec_sum["why_it_happened"]["bullets"]) >= 1
    assert len(exec_sum["possible_options_to_fix"]["options"]) >= 1
    assert "#### 1. What is the Problem" in result["narrative"]
    assert "#### 2. Why the Problem Happened" in result["narrative"]
    assert "#### 3. Possible Options to Fix the Problem" in result["narrative"]
