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
