"""IFSP V2 Planning Copilot — fast, deterministic-first multi-agent orchestrator.

Routes planner questions in <1ms using clean stopword-safe entity extraction,
persists multi-turn context via ConversationManager, and dispatches directly to
DuckDB-backed specialist agents (Fulfillment/Root-Cause, Validation, Scenario).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from ..agents.base import AgentContext, AgentResponse
from ..agents.bom_lineage import BomLineageAgent
from ..agents.capacity import CapacityAgent
from ..agents.fulfillment import FulfillmentAgent
from ..agents.inventory import InventoryAgent
from ..agents.scenario import ScenarioAgent
from ..agents.supply_orders import SupplyOrdersAgent
from ..agents.validation import ValidationAgent
from ..core.data import PlanningDataEngine, get_data_engine

_ITEM_STOPWORDS = frozenset({
    "item", "items", "for", "the", "a", "an", "this", "that", "these", "those",
    "all", "any", "each", "every", "some", "met", "meet", "unmet", "not",
    "was", "were", "are", "is", "be", "been", "being", "has", "have", "had",
    "do", "does", "did", "can", "could", "will", "would", "should", "may", "might",
    "check", "demand", "supply", "fulfilled", "short", "late", "early",
    "if", "it", "in", "or", "of", "at", "by", "on", "to", "with", "from", "into",
    "which", "where", "when", "why", "how", "what", "who",
    "product", "products", "order", "orders", "point", "level", "levels", "status",
})

_ITEM_EXPLICIT_RE = re.compile(r"\b(?:item|dmditem|product)\s*[:=#-]?\s*([A-Za-z0-9\-_]{5,})\b", re.IGNORECASE)
_ITEM_NUMERIC_RE = re.compile(r"\b(\d{6,})\b")
_SITE_RE = re.compile(r"\b(?:site|loc|location|plant)\s*[:=#-]?\s*([A-Za-z0-9\-_]{2,})\b", re.IGNORECASE)

_VALIDATION_KEYWORDS = (
    "validate", "validation", "referential integrity", "orphan", "master data",
    "data hygiene", "data quality", "readiness check", "bom integrity",
)
_SCENARIO_KEYWORDS = (
    "compare scenario", "scenario compare", "scenario comparison", "solve over solve",
    "week over week", "scenario delta", "versus", " vs ", "site mix", "which scenario",
)
_INVENTORY_KEYWORDS = (
    "inventory", "on hand", "on-hand", "eoh", "end of horizon", "end-of-horizon",
    "safety stock", "stockout", "stock out", "low stock", "projected oh",
)
_CAPACITY_KEYWORDS = (
    "capacity", "utilization", "overloaded", "underloaded", "overload", "underload",
    "resource bottleneck", "resource load", "res load", "res util", "bottleneck resource",
)
_SUPPLY_ORDER_KEYWORDS = (
    "planned order", "planned orders", "planorder", "planned purchase", "planned purchases",
    "planpurch", "planned arrival", "planarriv", "scheduled receipt", "schedrcpt",
    "make vs buy", "make or buy", "supply mix", "supply plan", "supply orders",
)
_BOM_LINEAGE_KEYWORDS = (
    "bom", "bill of material", "bill of materials", "alt bom", "alternate bom",
    "supersession", "subordinate", "where used", "where-used", "genealogy", "bom explosion",
)
_FULFILLMENT_KEYWORDS = (
    "unmet", "met or not", "meet or not", "met or unmet", "demand met", "demand meet",
    "demand are met", "demand are meet", "demand is met", "fill rate", "shortage",
    "shortfall", "late demand", "backorder", "fulfillment", "fulfilled",
    "order point", "why demand", "demand short", "why ",
)
_FOLLOWUP_CONTINUATION_PHRASES = (
    "across all", "for all item", "all items", "show me more", "tell me more",
    "details", "break it down", "breakdown", "elaborate",
)

# Per-session last active route so conversational follow-ups ("show me across all items")
# stay on the active domain (e.g. inventory -> inventory across all items).
_SESSION_LAST_ROUTE: Dict[str, str] = {}


def extract_item_id(question: str) -> Optional[str]:
    text = question or ""
    m = _ITEM_EXPLICIT_RE.search(text)
    if m:
        val = m.group(1).strip(" .,:;!?()[]{}")
        if val.lower() not in _ITEM_STOPWORDS and not (val.isdigit() and len(val) < 6):
            return val
    m2 = _ITEM_NUMERIC_RE.search(text)
    return m2.group(1) if m2 else None


def extract_location(question: str) -> Optional[str]:
    m = _SITE_RE.search(question or "")
    if not m:
        return None
    val = m.group(1).strip(" .,:;!?()[]{}")
    return None if val.lower() in _ITEM_STOPWORDS else val


def resolve_item_group(question: str, data: PlanningDataEngine) -> tuple[list[str], Optional[str]]:
    """Match product-family / ITEMCLASS / description terms in the question against if_snop_items."""
    if not data.has_table("if_snop_items"):
        return [], None
    ql = re.sub(r"[^a-z0-9\s]", " ", (question or "").lower())
    ql = re.sub(r"\s+", " ", ql).strip()
    if not ql:
        return [], None

    classes = data.query(
        'SELECT DISTINCT TRIM(ITEMCLASS) AS cls FROM "if_snop_items" WHERE TRIM(COALESCE(ITEMCLASS, \'\')) <> \'\''
    )
    matched_classes: list[tuple[int, str, str]] = []
    for row in classes:
        raw_cls = str(row.get("cls") or "").strip()
        if not raw_cls:
            continue
        # Normalize e.g. F_STACK_SILICON -> "stack silicon", SILICON_WAFER -> "silicon wafer"
        norm = re.sub(r"^(?:f_|proc_)+", "", raw_cls.lower()).replace("_", " ").strip()
        full_norm = raw_cls.lower().replace("_", " ").strip()
        for phrase in (full_norm, norm):
            if len(phrase) >= 4 and f" {phrase} " in f" {ql} ":
                matched_classes.append((len(phrase), raw_cls, phrase))
                break

    if not matched_classes:
        return [], None

    # Prefer the longest (most specific) matched item class
    matched_classes.sort(key=lambda x: x[0], reverse=True)
    _, best_cls, best_phrase = matched_classes[0]
    rows = data.query(
        'SELECT DISTINCT TRIM(ITEM) AS item FROM "if_snop_items" WHERE UPPER(TRIM(ITEMCLASS)) = UPPER(TRIM(?)) AND TRIM(COALESCE(ITEM, \'\')) <> \'\'',
        [best_cls],
    )
    items = [str(r["item"]) for r in rows if r.get("item")]
    label = f"{best_phrase.title()} (`{best_cls}`)"
    return items, label


_GREETING_EXACT = frozenset({
    "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
    "help", "start", "menu", "capabilities",
})
_GREETING_PHRASES = (
    "what can you do", "how do i use", "how can you help", "who are you",
    "what questions can i ask", "help me get started",
)
_DATASET_PHRASES = (
    "what datasets", "which datasets", "list datasets", "dataset summary",
    "datasets available", "available tables", "show tables", "what tables",
    "what data is loaded", "what data do we have",
)


class PlanningCopilotV2:
    """Clean, fast V2 orchestrator for IFSP planning explainability."""

    def __init__(self, base_dir: Path, data_engine: Optional[PlanningDataEngine] = None) -> None:
        self.base_dir = Path(base_dir)
        self.data = data_engine or get_data_engine(self.base_dir)
        self.fulfillment = FulfillmentAgent(self.data)
        self.inventory = InventoryAgent(self.data)
        self.capacity = CapacityAgent(self.data)
        self.supply_orders = SupplyOrdersAgent(self.data)
        self.bom_lineage = BomLineageAgent(self.data)
        self.validation = ValidationAgent(self.data)
        self.scenario = ScenarioAgent(self.data)

    def _handle_greeting_or_help(self) -> AgentResponse:
        cat = self.data.catalog_summary()
        return AgentResponse(
            agent="PlanningCopilotV2",
            workflow="Assistant Guide",
            summary=(
                f"Hi! I'm your **Intel Foundry Supply Planning AI Assistant**, connected to "
                f"**{cat['by_input_count']} input** and **{cat['by_output_count']} output** BY ESP planning snapshots."
            ),
            confirmed_findings=[
                "**Demand & Fulfillment:** Ask whether demands are met across all items, a specific item ID, or an item class (e.g. *Stack Silicon*, *Silicon Wafer*, *Die Prep*).",
                "**Root-Cause Diagnosis:** Ask *why* demand is unmet or late to see SKU exceptions and resource capacity bottlenecks.",
                "**Validation & Scenarios:** Run master data / BOM integrity checks or compare scenario KPIs.",
            ],
            evidence_sources=["BY ESP Catalog"],
            confidence="High",
            recommended_next_checks=[
                "Check if demands are met for all order point items",
                "Explain why demand is unmet for Stack Silicon items",
                "Run master data validation check",
                "Compare scenario performance",
            ],
            duration_ms=1,
        )

    def _handle_dataset_summary(self) -> AgentResponse:
        cat = self.data.catalog_summary()
        in_names = ", ".join(f"`{t['table']}`" for t in cat["by_input"][:6])
        out_names = ", ".join(f"`{t['table']}`" for t in cat["by_output"][:6])
        return AgentResponse(
            agent="PlanningCopilotV2",
            workflow="Dataset Inventory",
            summary=(
                f"Loaded **{cat['total_views']} BY ESP planning tables** "
                f"(**{cat['by_input_count']}** in `by_input/` and **{cat['by_output_count']}** in `by_output/`)."
            ),
            confirmed_findings=[
                f"Sample input tables: {in_names}" + (f" (+{cat['by_input_count'] - 6} more)" if cat["by_input_count"] > 6 else ""),
                f"Sample output tables: {out_names}" + (f" (+{cat['by_output_count'] - 6} more)" if cat["by_output_count"] > 6 else ""),
            ],
            metrics={"total_views": cat["total_views"], "by_input": cat["by_input_count"], "by_output": cat["by_output_count"]},
            evidence_sources=["by_input", "by_output"],
            confidence="High",
            recommended_next_checks=[
                "Check if demands are met for all order point items",
                "Run master data validation check",
                "Compare scenario performance",
            ],
            duration_ms=2,
        )

    def classify_route(
        self,
        question: str,
        item_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """Classify into a V2 specialist route or None."""
        raw = re.sub(r"[?!.,]+$", "", (question or "").strip().lower()).strip()
        if raw in _GREETING_EXACT or any(p in raw for p in _GREETING_PHRASES):
            return "greeting"
        if any(p in raw for p in _DATASET_PHRASES):
            return "datasets"
        ql = f" {raw} "
        if any(k in ql for k in _VALIDATION_KEYWORDS):
            return "validation"
        if any(k in ql for k in _SUPPLY_ORDER_KEYWORDS):
            return "supply_orders"
        if any(k in ql for k in _BOM_LINEAGE_KEYWORDS):
            return "bom_lineage"
        if any(k in ql for k in _SCENARIO_KEYWORDS):
            return "scenario"
        if any(k in ql for k in _INVENTORY_KEYWORDS):
            return "inventory"
        if any(k in ql for k in _CAPACITY_KEYWORDS):
            return "capacity"
        if any(k in ql for k in _FULFILLMENT_KEYWORDS) or item_id:
            return "fulfillment"
        # Carry forward active topic for natural follow-ups like "show me across all items"
        if session_id and session_id in _SESSION_LAST_ROUTE:
            if any(p in raw for p in _FOLLOWUP_CONTINUATION_PHRASES):
                return _SESSION_LAST_ROUTE[session_id]
        return None

    def handle(
        self,
        question: str,
        session_id: str = "default",
        week_id: Optional[str] = None,
        scenario_id: Optional[str] = None,
        scope: Optional[Dict[str, Any]] = None,
        show_detailed_analysis: bool = False,
        fallback_item_id: Optional[str] = None,
    ) -> Optional[AgentResponse]:
        """Execute the matching V2 specialist agent, or return None to let legacy handle it."""
        scope = scope or {}
        item_id = extract_item_id(question) or (scope.get("item") or "").strip() or None
        location = extract_location(question) or (scope.get("site") or "").strip() or None
        item_ids: list[str] = []
        item_filter_label: Optional[str] = None

        if not item_id:
            item_ids, item_filter_label = resolve_item_group(question, self.data)

        # Only carry forward prior item for explicit item follow-ups (not portfolio or item-group queries)
        ql = (question or "").lower()
        is_portfolio = any(p in ql for p in ("all item", "all order", "overall", "portfolio", "across all"))
        if not item_id and not item_ids and not is_portfolio and fallback_item_id:
            if any(p in ql for p in ("why", "it", "that", "this", "short", "unmet", "late", "constraint")):
                item_id = fallback_item_id

        route = self.classify_route(
            question,
            item_id=item_id or (item_ids[0] if item_ids else None),
            session_id=session_id,
        )
        if not route:
            return None

        if route == "greeting":
            return self._handle_greeting_or_help()
        if route == "datasets":
            return self._handle_dataset_summary()

        _SESSION_LAST_ROUTE[session_id] = route

        ctx = AgentContext(
            question=question,
            session_id=session_id,
            item_id=item_id,
            item_ids=item_ids,
            item_filter_label=item_filter_label,
            location=location,
            week_id=week_id,
            scenario_id=scenario_id,
            show_detailed_analysis=show_detailed_analysis,
        )

        if route == "validation":
            return self.validation.run(ctx)
        if route == "scenario":
            return self.scenario.run(ctx)
        if route == "inventory":
            return self.inventory.run(ctx)
        if route == "capacity":
            return self.capacity.run(ctx)
        if route == "supply_orders":
            return self.supply_orders.run(ctx)
        if route == "bom_lineage":
            return self.bom_lineage.run(ctx)
        return self.fulfillment.run(ctx)
