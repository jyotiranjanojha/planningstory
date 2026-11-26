"""DuckDB-powered Supply Plan & Make-vs-Buy Orders Agent (IFSP V2)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from ..core.data import PlanningDataEngine
from .base import AgentContext, AgentResponse
from .fulfillment import _build_scope_filters, _fmt_num


class SupplyOrdersAgent:
    """Fast DuckDB specialist for planned orders, planned purchases, arrivals, and scheduled receipts."""

    PLAN_ORDER_TABLE = "by_if_snop_out_planorder"
    PLAN_PURCH_TABLE = "by_if_snop_out_planpurch"
    PLAN_ARRIV_TABLE = "by_if_snop_out_planarriv"
    SCHED_RCPT_TABLE = "if_snop_schedrcpts"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def _agg_table(self, table: str, ctx: AgentContext) -> Dict[str, Any]:
        if not self.data.has_table(table):
            return {"lines": 0, "items": 0, "qty": 0.0, "present": False}
        meta = self.data.table_meta(table)
        where_sql, params = _build_scope_filters(ctx, meta.columns if meta else ())
        row = self.data.query_one(
            f"""
            SELECT
                COUNT(*) AS lines,
                COUNT(DISTINCT ITEM) AS items,
                COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0) AS qty
            FROM "{table}"
            {where_sql}
            """,
            params,
        ) or {}
        return {
            "lines": int(row.get("lines") or 0),
            "items": int(row.get("items") or 0),
            "qty": float(row.get("qty") or 0),
            "present": True,
        }

    def run(self, ctx: AgentContext) -> AgentResponse:
        t0 = time.perf_counter()
        po = self._agg_table(self.PLAN_ORDER_TABLE, ctx)
        pp = self._agg_table(self.PLAN_PURCH_TABLE, ctx)
        pa = self._agg_table(self.PLAN_ARRIV_TABLE, ctx)
        sr = self._agg_table(self.SCHED_RCPT_TABLE, ctx)

        evidence = [
            tbl for tbl, stat in [
                (self.PLAN_ORDER_TABLE, po),
                (self.PLAN_PURCH_TABLE, pp),
                (self.PLAN_ARRIV_TABLE, pa),
                (self.SCHED_RCPT_TABLE, sr),
            ]
            if stat["present"]
        ]

        total_supply = po["qty"] + pp["qty"] + pa["qty"] + sr["qty"]
        make_pct = (po["qty"] / total_supply * 100.0) if total_supply > 0 else 0.0
        buy_pct = (pp["qty"] / total_supply * 100.0) if total_supply > 0 else 0.0

        findings: List[str] = [
            f"**Planned Manufacturing Orders (`planorder`):** {_fmt_num(po['qty'])} units across {_fmt_num(po['lines'])} orders ({_fmt_num(po['items'])} items).",
            f"**Planned Purchases (`planpurch`):** {_fmt_num(pp['qty'])} units across {_fmt_num(pp['lines'])} orders ({_fmt_num(pp['items'])} items).",
            f"**Planned Arrivals / Transfers (`planarriv`):** {_fmt_num(pa['qty'])} units across {_fmt_num(pa['lines'])} transfers ({_fmt_num(pa['items'])} items).",
            f"**Scheduled Receipts (`schedrcpts`):** {_fmt_num(sr['qty'])} units across {_fmt_num(sr['lines'])} receipts ({_fmt_num(sr['items'])} items).",
        ]

        top_rows: List[Dict[str, Any]] = []
        if po["present"] and po["lines"] > 0:
            meta = self.data.table_meta(self.PLAN_ORDER_TABLE)
            where_sql, params = _build_scope_filters(ctx, meta.columns if meta else ())
            top_rows = self.data.query(
                f"""
                SELECT
                    TRIM(ITEM) AS item,
                    TRIM(LOC) AS loc,
                    COUNT(*) AS orders,
                    ROUND(COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0), 2) AS planned_qty
                FROM "{self.PLAN_ORDER_TABLE}"
                {where_sql}
                GROUP BY 1, 2
                ORDER BY planned_qty DESC
                LIMIT 8
                """,
                params,
            )
            if top_rows:
                top_str = "; ".join(f"`{r['item']}` @ `{r['loc']}` ({_fmt_num(r['planned_qty'])})" for r in top_rows[:5])
                findings.append(f"Top planned manufacturing items: {top_str}.")

        scope_title = (
            f"Item `{ctx.item_id}`"
            if ctx.item_id
            else (ctx.item_filter_label or "All items")
        )

        summary = (
            f"**Supply Plan & Orders — {scope_title}:** Total Supply **{_fmt_num(total_supply)}** units "
            f"(Make: **{make_pct:.1f}%**, Buy: **{buy_pct:.1f}%**, Transfers: **{_fmt_num(pa['qty'])}**, Receipts: **{_fmt_num(sr['qty'])}**)."
        )

        next_checks = [
            "Check if demands are met for all order point items",
            "Which resources have capacity bottlenecks?",
            "Show BOM structure and alternate BOM summary",
        ]

        return AgentResponse(
            agent="SupplyOrdersAgent",
            workflow="Supply Plan & Orders (V2)",
            summary=summary,
            confirmed_findings=findings,
            metrics={
                "total_supply_qty": round(total_supply, 2),
                "planorder_qty": round(po["qty"], 2),
                "planpurch_qty": round(pp["qty"], 2),
                "planarriv_qty": round(pa["qty"], 2),
                "schedrcpts_qty": round(sr["qty"], 2),
                "make_pct": round(make_pct, 1),
                "buy_pct": round(buy_pct, 1),
            },
            rows=top_rows,
            evidence_sources=evidence,
            confidence="High" if evidence else "Low",
            recommended_next_checks=next_checks,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
