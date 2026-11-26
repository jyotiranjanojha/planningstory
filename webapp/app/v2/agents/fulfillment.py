"""DuckDB-powered Fulfillment & Unmet-Demand Root Cause Agent (IFSP V2)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from ..core.data import PlanningDataEngine
from .base import AgentContext, AgentResponse


def _build_scope_filters(ctx: AgentContext, table_cols: tuple[str, ...]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    col_set = {c.upper() for c in table_cols}

    if ctx.item_id and "ITEM" in col_set:
        clauses.append("UPPER(TRIM(ITEM)) = UPPER(TRIM(?))")
        params.append(ctx.item_id)
    elif ctx.item_ids and "ITEM" in col_set:
        placeholders = ", ".join(["UPPER(TRIM(?))"] * len(ctx.item_ids))
        clauses.append(f"UPPER(TRIM(ITEM)) IN ({placeholders})")
        params.extend(ctx.item_ids)
    if ctx.location and "LOC" in col_set:
        clauses.append("UPPER(TRIM(LOC)) = UPPER(TRIM(?))")
        params.append(ctx.location)
    if ctx.week_id and "CAPTURE_WK" in col_set:
        clauses.append("TRIM(CAPTURE_WK) = TRIM(?)")
        params.append(ctx.week_id)
    if ctx.scenario_id and "SIMULATION_NAME" in col_set:
        clauses.append("UPPER(TRIM(SIMULATION_NAME)) = UPPER(TRIM(?))")
        params.append(ctx.scenario_id)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def _fmt_num(val: Any) -> str:
    try:
        f = float(val or 0)
        return f"{int(round(f)):,}" if abs(f - round(f)) < 1e-6 else f"{f:,.2f}"
    except (TypeError, ValueError):
        return str(val)


class FulfillmentAgent:
    """Fast DuckDB specialist for portfolio/item demand fulfillment and unmet root-cause."""

    DEMAND_TABLE = "by_if_snop_out_inddmdview"
    DEP_DEMAND_TABLE = "by_if_snop_out_depdmdstatic"
    SKU_EXC_TABLE = "by_if_snop_out_skuexception"
    RES_EXC_TABLE = "by_if_snop_out_resexception"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def run(self, ctx: AgentContext) -> AgentResponse:
        t0 = time.perf_counter()
        if not self.data.has_table(self.DEMAND_TABLE):
            return AgentResponse(
                agent="FulfillmentAgent",
                workflow="Fulfillment & Root Cause (V2)",
                summary="Demand output snapshot (`by_if_snop_out_inddmdview`) is not available in `by_output/`.",
                confidence="Low",
                data_gaps=[f"Missing {self.DEMAND_TABLE} in by_output/"],
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        meta = self.data.table_meta(self.DEMAND_TABLE)
        where_sql, params = _build_scope_filters(ctx, meta.columns if meta else ())

        summary_row = self.data.query_one(
            f"""
            SELECT
                COUNT(*) AS order_lines,
                COUNT(DISTINCT ITEM) AS distinct_items,
                COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0) AS total_demand,
                COALESCE(SUM(TRY_CAST(SCHEDQTY AS DOUBLE)), 0) AS total_scheduled,
                COALESCE(SUM(GREATEST(TRY_CAST(QTY AS DOUBLE) - COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0), 0)), 0) AS total_unmet,
                SUM(CASE WHEN COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0) >= TRY_CAST(QTY AS DOUBLE) AND TRY_CAST(QTY AS DOUBLE) > 0 THEN 1 ELSE 0 END) AS met_lines,
                SUM(CASE WHEN COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0) > 0 AND COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0) < TRY_CAST(QTY AS DOUBLE) THEN 1 ELSE 0 END) AS partial_lines,
                SUM(CASE WHEN COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0) <= 0 AND TRY_CAST(QTY AS DOUBLE) > 0 THEN 1 ELSE 0 END) AS unmet_lines
            FROM "{self.DEMAND_TABLE}"
            {where_sql}
            """,
            params,
        ) or {}

        total_demand = float(summary_row.get("total_demand") or 0)
        total_sched = float(summary_row.get("total_scheduled") or 0)
        total_unmet = float(summary_row.get("total_unmet") or 0)
        fill_rate = (total_sched / total_demand * 100.0) if total_demand > 0 else 0.0
        order_lines = int(summary_row.get("order_lines") or 0)
        distinct_items = int(summary_row.get("distinct_items") or 0)
        met_lines = int(summary_row.get("met_lines") or 0)
        partial_lines = int(summary_row.get("partial_lines") or 0)
        unmet_lines = int(summary_row.get("unmet_lines") or 0)

        if order_lines == 0:
            scope_desc = (
                f"item `{ctx.item_id}`"
                if ctx.item_id
                else (ctx.item_filter_label or "the requested scope")
            )
            # Check dependent demand & exceptions for intermediate/subordinate items (e.g. F_STACK_SILICON)
            dep_findings: List[str] = []
            dep_causes: List[str] = []
            dep_sources = [self.DEMAND_TABLE]
            dep_rows: List[Dict[str, Any]] = []

            if self.data.has_table(self.DEP_DEMAND_TABLE):
                dep_meta = self.data.table_meta(self.DEP_DEMAND_TABLE)
                dep_where, dep_params = _build_scope_filters(ctx, dep_meta.columns if dep_meta else ())
                dep_summary = self.data.query_one(
                    f"""
                    SELECT
                        COUNT(*) AS dep_lines,
                        COUNT(DISTINCT ITEM) AS dep_items,
                        COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0) AS dep_qty
                    FROM "{self.DEP_DEMAND_TABLE}"
                    {dep_where}
                    """,
                    dep_params,
                ) or {}
                dep_lines = int(dep_summary.get("dep_lines") or 0)
                if dep_lines > 0:
                    dep_sources.append(self.DEP_DEMAND_TABLE)
                    dep_findings.append(
                        f"Found **{_fmt_num(dep_lines)}** dependent demand lines totaling **{_fmt_num(dep_summary.get('dep_qty'))}** units across **{_fmt_num(dep_summary.get('dep_items'))}** item(s) in `{self.DEP_DEMAND_TABLE}`."
                    )
                    dep_rows = self.data.query(
                        f"""
                        SELECT ITEM AS entity, COUNT(*) AS lines, ROUND(COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0), 2) AS dep_demand_qty
                        FROM "{self.DEP_DEMAND_TABLE}"
                        {dep_where}
                        GROUP BY ITEM
                        ORDER BY dep_demand_qty DESC
                        LIMIT 8
                        """,
                        dep_params,
                    )

            if self.data.has_table(self.SKU_EXC_TABLE):
                exc_meta = self.data.table_meta(self.SKU_EXC_TABLE)
                exc_where, exc_params = _build_scope_filters(ctx, exc_meta.columns if exc_meta else ())
                for row in self.data.query(
                    f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'Unknown SKU Exception') AS reason,
                        COUNT(*) AS occurrences,
                        COUNT(DISTINCT ITEM) AS items_affected
                    FROM "{self.SKU_EXC_TABLE}"
                    {exc_where}
                    GROUP BY 1
                    ORDER BY occurrences DESC
                    LIMIT 5
                    """,
                    exc_params,
                ):
                    if self.SKU_EXC_TABLE not in dep_sources:
                        dep_sources.append(self.SKU_EXC_TABLE)
                    dep_causes.append(
                        f"SKU Exception: **{row['reason']}** ({_fmt_num(row['occurrences'])} occurrences across {_fmt_num(row['items_affected'])} item(s))"
                    )

            if self.data.has_table(self.RES_EXC_TABLE):
                res_meta = self.data.table_meta(self.RES_EXC_TABLE)
                res_where, res_params = _build_scope_filters(ctx, res_meta.columns if res_meta else ())
                for row in self.data.query(
                    f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'Resource Constraint') AS reason,
                        COUNT(*) AS occurrences,
                        COUNT(DISTINCT RES) AS resources_affected
                    FROM "{self.RES_EXC_TABLE}"
                    {res_where}
                    GROUP BY 1
                    ORDER BY occurrences DESC
                    LIMIT 3
                    """,
                    res_params,
                ):
                    if self.RES_EXC_TABLE not in dep_sources:
                        dep_sources.append(self.RES_EXC_TABLE)
                    dep_causes.append(
                        f"Resource Exception: **{row['reason']}** ({_fmt_num(row['occurrences'])} occurrences across {_fmt_num(row['resources_affected'])} resource(s))"
                    )

            matched_items_note = (
                f"Matched {len(ctx.item_ids)} item(s): {', '.join(f'`{i}`' for i in ctx.item_ids[:6])}."
                if ctx.item_ids else ""
            )
            if matched_items_note:
                dep_findings.insert(0, matched_items_note)

            return AgentResponse(
                agent="FulfillmentAgent",
                workflow="Fulfillment & Root Cause (V2)",
                summary=(
                    f"**{scope_desc}** has **0 independent demand rows** in `{self.DEMAND_TABLE}` "
                    f"(these are intermediate/component items rather than top-level independent demand)."
                ),
                confirmed_findings=dep_findings,
                root_causes=dep_causes,
                rows=dep_rows,
                evidence_sources=dep_sources,
                confidence="High" if (dep_findings or dep_causes) else "Medium",
                data_gaps=[] if dep_causes else [f"No independent demand rows for {scope_desc} in {self.DEMAND_TABLE}"],
                recommended_next_checks=[
                    f"Trace parent BOM demand for `{ctx.item_ids[0]}` to see which finished-good orders depend on it."
                    if ctx.item_ids else "Verify the item ID, item class, week, or scenario filter."
                ],
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        # Top unmet items (or location breakdown if a single item is scoped)
        group_col = "LOC" if ctx.item_id else "ITEM"
        top_unmet = self.data.query(
            f"""
            SELECT
                {group_col} AS entity,
                COUNT(*) AS lines,
                ROUND(COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0), 2) AS demand_qty,
                ROUND(COALESCE(SUM(TRY_CAST(SCHEDQTY AS DOUBLE)), 0), 2) AS scheduled_qty,
                ROUND(COALESCE(SUM(GREATEST(TRY_CAST(QTY AS DOUBLE) - COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0), 0)), 0), 2) AS unmet_qty
            FROM "{self.DEMAND_TABLE}"
            {where_sql}
            GROUP BY {group_col}
            HAVING SUM(GREATEST(TRY_CAST(QTY AS DOUBLE) - COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0), 0)) > 0
            ORDER BY unmet_qty DESC
            LIMIT 8
            """,
            params,
        )

        # Root-cause drivers from SKU exceptions and Resource exceptions
        evidence_sources = [self.DEMAND_TABLE]
        root_causes: List[str] = []

        if self.data.has_table(self.SKU_EXC_TABLE):
            exc_meta = self.data.table_meta(self.SKU_EXC_TABLE)
            exc_where, exc_params = _build_scope_filters(ctx, exc_meta.columns if exc_meta else ())
            sku_excs = self.data.query(
                f"""
                SELECT
                    COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'Unknown SKU Exception') AS reason,
                    COUNT(*) AS occurrences,
                    COUNT(DISTINCT ITEM) AS items_affected
                FROM "{self.SKU_EXC_TABLE}"
                {exc_where}
                GROUP BY 1
                ORDER BY occurrences DESC
                LIMIT 5
                """,
                exc_params,
            )
            if sku_excs:
                evidence_sources.append(self.SKU_EXC_TABLE)
                for row in sku_excs:
                    root_causes.append(
                        f"SKU Exception: **{row['reason']}** ({_fmt_num(row['occurrences'])} occurrences across {_fmt_num(row['items_affected'])} item(s))"
                    )

        if self.data.has_table(self.RES_EXC_TABLE):
            res_meta = self.data.table_meta(self.RES_EXC_TABLE)
            res_where, res_params = _build_scope_filters(ctx, res_meta.columns if res_meta else ())
            res_excs = self.data.query(
                f"""
                SELECT
                    COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'Resource Constraint') AS reason,
                    COUNT(*) AS occurrences,
                    COUNT(DISTINCT RES) AS resources_affected
                FROM "{self.RES_EXC_TABLE}"
                {res_where}
                GROUP BY 1
                ORDER BY occurrences DESC
                LIMIT 3
                """,
                res_params,
            )
            if res_excs:
                evidence_sources.append(self.RES_EXC_TABLE)
                for row in res_excs:
                    root_causes.append(
                        f"Resource Exception: **{row['reason']}** ({_fmt_num(row['occurrences'])} occurrences across {_fmt_num(row['resources_affected'])} resource(s))"
                    )

        status_label = (
            "Fully Met" if total_unmet <= 0
            else ("Unmet" if total_sched <= 0 else "Partially Met")
        )
        scope_title = (
            f"Item `{ctx.item_id}`"
            if ctx.item_id
            else (
                f"{ctx.item_filter_label} ({_fmt_num(distinct_items)} matching item(s))"
                if ctx.item_filter_label
                else f"All items ({_fmt_num(distinct_items)} distinct items)"
            )
        )

        summary = (
            f"**{scope_title} — Demand Status: {status_label}** | "
            f"Fill Rate: **{fill_rate:.1f}%** "
            f"(Demand: **{_fmt_num(total_demand)}**, Scheduled: **{_fmt_num(total_sched)}**, "
            f"Unmet: **{_fmt_num(total_unmet)}** across {_fmt_num(order_lines)} demand lines)."
        )

        findings = [
            f"Line split: **{_fmt_num(met_lines)}** met, **{_fmt_num(partial_lines)}** partially met, **{_fmt_num(unmet_lines)}** completely unmet.",
        ]
        if top_unmet:
            top_label = "Top unmet locations" if ctx.item_id else "Top unmet items"
            top_str = "; ".join(
                f"`{r['entity']}` (unmet {_fmt_num(r['unmet_qty'])} / demand {_fmt_num(r['demand_qty'])})"
                for r in top_unmet[:5]
            )
            findings.append(f"{top_label}: {top_str}.")

        next_checks: List[str] = []
        if not ctx.item_id and top_unmet:
            top_item = top_unmet[0]["entity"]
            next_checks.append(f"Drill into the largest unmet item: *\"Why is demand unmet for item {top_item}?\"*")
        if root_causes:
            next_checks.append("Review resource capacity exceptions and BOM/sourcing validity for the top unmet items.")
        else:
            next_checks.append("Run master data / BOM validation to check for missing supply paths or parameters.")

        return AgentResponse(
            agent="FulfillmentAgent",
            workflow="Fulfillment & Root Cause (V2)",
            summary=summary,
            confirmed_findings=findings,
            root_causes=root_causes,
            metrics={
                "status": status_label,
                "fill_rate_pct": round(fill_rate, 2),
                "total_demand": round(total_demand, 2),
                "total_scheduled": round(total_sched, 2),
                "total_unmet": round(total_unmet, 2),
                "order_lines": order_lines,
                "distinct_items": distinct_items,
                "met_lines": met_lines,
                "partial_lines": partial_lines,
                "unmet_lines": unmet_lines,
            },
            rows=top_unmet,
            evidence_sources=evidence_sources,
            confidence="High",
            recommended_next_checks=next_checks,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
