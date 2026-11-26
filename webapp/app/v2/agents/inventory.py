"""DuckDB-powered Inventory Position & Stockout Risk Agent (IFSP V2)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from ..core.data import PlanningDataEngine
from .base import AgentContext, AgentResponse
from .fulfillment import _build_scope_filters, _fmt_num


class InventoryAgent:
    """Fast DuckDB specialist for on-hand inventory, EOH, and stockout/low-stock risk."""

    INV_TABLE = "if_snop_inventory"
    SKUSTAT_TABLE = "by_if_snop_out_skustatstatic"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def run(self, ctx: AgentContext) -> AgentResponse:
        t0 = time.perf_counter()
        evidence: List[str] = []
        findings: List[str] = []
        root_causes: List[str] = []
        top_rows: List[Dict[str, Any]] = []

        total_oh = 0.0
        inv_items = 0
        inv_locs = 0

        if self.data.has_table(self.INV_TABLE):
            evidence.append(self.INV_TABLE)
            meta = self.data.table_meta(self.INV_TABLE)
            where_sql, params = _build_scope_filters(ctx, meta.columns if meta else ())
            agg = self.data.query_one(
                f"""
                SELECT
                    COUNT(*) AS rows,
                    COUNT(DISTINCT ITEM) AS items,
                    COUNT(DISTINCT LOC) AS locs,
                    COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0) AS total_qty
                FROM "{self.INV_TABLE}"
                {where_sql}
                """,
                params,
            ) or {}
            total_oh = float(agg.get("total_qty") or 0)
            inv_items = int(agg.get("items") or 0)
            inv_locs = int(agg.get("locs") or 0)

            group_col = "LOC" if ctx.item_id else "ITEM"
            top_rows = self.data.query(
                f"""
                SELECT
                    TRIM({group_col}) AS entity,
                    COUNT(DISTINCT LOC) AS locations,
                    ROUND(COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0), 2) AS on_hand_qty
                FROM "{self.INV_TABLE}"
                {where_sql}
                GROUP BY 1
                ORDER BY on_hand_qty DESC
                LIMIT 8
                """,
                params,
            )
            if top_rows:
                lbl = "Top on-hand locations" if ctx.item_id else "Top on-hand items"
                top_str = "; ".join(f"`{r['entity']}` ({_fmt_num(r['on_hand_qty'])})" for r in top_rows[:5])
                findings.append(f"{lbl}: {top_str}.")

        stockout_skus = 0
        low_stock_skus = 0
        if self.data.has_table(self.SKUSTAT_TABLE):
            evidence.append(self.SKUSTAT_TABLE)
            smeta = self.data.table_meta(self.SKUSTAT_TABLE)
            swhere, sparams = _build_scope_filters(ctx, smeta.columns if smeta else ())
            stat = self.data.query_one(
                f"""
                SELECT
                    COUNT(*) AS sku_rows,
                    SUM(CASE WHEN COALESCE(TRY_CAST(CONSTRSTOCKOUTQTY AS DOUBLE), 0) > 0 THEN 1 ELSE 0 END) AS stockout_skus,
                    SUM(CASE WHEN COALESCE(TRY_CAST(CONSTRSTOCKLOWQTY AS DOUBLE), 0) > 0 THEN 1 ELSE 0 END) AS low_stock_skus,
                    COALESCE(SUM(TRY_CAST(CONSTRSTOCKOUTQTY AS DOUBLE)), 0) AS total_stockout_qty,
                    COALESCE(MAX(TRY_CAST(MAXCONSTRPROJOH AS DOUBLE)), 0) AS max_proj_oh
                FROM "{self.SKUSTAT_TABLE}"
                {swhere}
                """,
                sparams,
            ) or {}
            stockout_skus = int(stat.get("stockout_skus") or 0)
            low_stock_skus = int(stat.get("low_stock_skus") or 0)
            total_so_qty = float(stat.get("total_stockout_qty") or 0)
            if stockout_skus > 0 or low_stock_skus > 0:
                findings.append(
                    f"Projected risk (`{self.SKUSTAT_TABLE}`): **{_fmt_num(stockout_skus)}** SKU-location(s) hit stockout "
                    f"(total stockout qty **{_fmt_num(total_so_qty)}**) and **{_fmt_num(low_stock_skus)}** fall below safety stock."
                )

            worst_so = self.data.query(
                f"""
                SELECT
                    TRIM(ITEM) AS item,
                    TRIM(LOC) AS loc,
                    ROUND(COALESCE(SUM(TRY_CAST(CONSTRSTOCKOUTQTY AS DOUBLE)), 0), 2) AS stockout_qty,
                    ROUND(COALESCE(SUM(TRY_CAST(CONSTRSTOCKLOWQTY AS DOUBLE)), 0), 2) AS stocklow_qty
                FROM "{self.SKUSTAT_TABLE}"
                {swhere}
                GROUP BY 1, 2
                HAVING SUM(COALESCE(TRY_CAST(CONSTRSTOCKOUTQTY AS DOUBLE), 0)) > 0
                    OR SUM(COALESCE(TRY_CAST(CONSTRSTOCKLOWQTY AS DOUBLE), 0)) > 0
                ORDER BY stockout_qty DESC, stocklow_qty DESC
                LIMIT 5
                """,
                sparams,
            )
            for row in worst_so:
                root_causes.append(
                    f"`{row['item']}` @ `{row['loc']}`: stockout **{_fmt_num(row['stockout_qty'])}**, low-stock **{_fmt_num(row['stocklow_qty'])}**"
                )

        scope_title = (
            f"Item `{ctx.item_id}`"
            if ctx.item_id
            else (ctx.item_filter_label or f"All items ({_fmt_num(inv_items)} items across {_fmt_num(inv_locs)} locations)")
        )

        summary = (
            f"**Inventory Position — {scope_title}:** Total On-Hand Qty is **{_fmt_num(total_oh)}** "
            f"(Stockout SKUs: **{_fmt_num(stockout_skus)}**, Low-Stock SKUs: **{_fmt_num(low_stock_skus)}**)."
        )

        next_checks = [
            "Check if demands are met for all order point items",
            "Which resources have capacity bottlenecks?",
        ]
        if top_rows and not ctx.item_id:
            next_checks.insert(0, f"Why is demand unmet for item {top_rows[0]['entity']}?")

        return AgentResponse(
            agent="InventoryAgent",
            workflow="Inventory Position (V2)",
            summary=summary,
            confirmed_findings=findings,
            root_causes=root_causes,
            metrics={
                "total_on_hand": round(total_oh, 2),
                "distinct_items": inv_items,
                "distinct_locations": inv_locs,
                "stockout_skus": stockout_skus,
                "low_stock_skus": low_stock_skus,
            },
            rows=top_rows,
            evidence_sources=evidence,
            confidence="High" if evidence else "Low",
            recommended_next_checks=next_checks,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
