"""FastAPI routes for IFSP V2 Core (/api/v2/*)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .agents.graph import KnowledgeGraphAgent
from .core.data import get_data_engine
from .core.llm import UnifiedLLMClient
from .orchestrator.copilot import PlanningCopilotV2

BASE_DIR = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api/v2", tags=["v2-core"])


class V2ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = "default"
    week_id: Optional[str] = None
    scenario_id: Optional[str] = None
    scope: Dict[str, Any] = Field(default_factory=dict)
    show_detailed_analysis: bool = False


def get_copilot_v2() -> PlanningCopilotV2:
    return PlanningCopilotV2(BASE_DIR)


@router.get("/health")
def v2_health():
    engine = get_data_engine(BASE_DIR)
    llm = UnifiedLLMClient()
    return {
        "status": "ok",
        "engine": "ifsp-v2-core",
        "duckdb_views": engine.catalog_summary()["total_views"],
        "llm_provider": llm.settings.provider,
        "llm_model": llm.settings.model,
    }


@router.get("/catalog")
def v2_catalog():
    return get_data_engine(BASE_DIR).catalog_summary()


@router.get("/graph/items")
def v2_graph_items(limit: int = 50):
    agent = KnowledgeGraphAgent(get_data_engine(BASE_DIR))
    items = agent.list_selectable_items(limit=limit)
    return {"count": len(items), "items": items}


@router.post("/chat")
def v2_chat(req: V2ChatRequest):
    copilot = get_copilot_v2()
    res = copilot.handle(
        question=req.question,
        session_id=req.session_id or "default",
        week_id=req.week_id,
        scenario_id=req.scenario_id,
        scope=req.scope,
        show_detailed_analysis=req.show_detailed_analysis,
    )
    if res is None:
        return {
            "handled": False,
            "reply": "Query did not match a V2 deterministic workflow.",
        }
    return {
        "handled": True,
        "reply": res.to_markdown(detailed=req.show_detailed_analysis),
        "result": res.model_dump(),
    }
