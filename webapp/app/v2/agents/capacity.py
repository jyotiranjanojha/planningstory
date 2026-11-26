"""DuckDB-powered Resource Capacity & Utilization Agent (IFSP V2)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from ..core.data import PlanningDataEngine
from .base import AgentContext, AgentResponse
from .fulfillment import _build_scope_filters, _fmt_num


class CapacityAgent:
    """Fast DuckDB specialist for resource utilization, overload/underload, and capacity exceptions."""

    RES_PROJ_TABLE = "by_if_snop_out_resprojstatic"
    RES_EXC_TABLE = "by_if_snop_out_resexception"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def run(self, ctx: AgentContext) -> AgentResponse:
        t0 = time.perf_counter()
        evidence: List[str] = []
        findings: List[str] = []
        root_causes: List[str] = []
        top_rows: List[Dict[str, Any]] = []

        distinct_res = 0
        avg_util = 0.0
        overloaded_buckets = 0
        underloaded_buckets = 0

        if self.data.has_table(self.RES_PROJ_TABLE):
            evidence.append(self.RES_PROJ_TABLE)
            meta = self.data.table_meta(self.RES_PROJ_TABLE)
            where_sql, params = _build_scope_filters(ctx, meta.columns if meta else ())
            if ctx.resource:
                conj = " AND " if where_sql else "WHERE "
                where_sql += f"{conj}UPPER(TRIM(RES)) = UPPER(TRIM(?))"
                params.append(ctx.resource)

            agg = self.data.query_one(
                f"""
                SELECT
                    COUNT(DISTINCT RES) AS distinct_res,
                    ROUND(COALESCE(AVG(TRY_CAST(PCTUSED AS DOUBLE)), 0), 1) AS avg_util,
                    SUM(CASE WHEN COALESCE(TRY_CAST(PCTUSED AS DOUBLE), 0) >= 95 THEN 1 ELSE 0 END) AS overloaded_buckets,
                    SUM(CASE WHEN COALESCE(TRY_CAST(PCTUSED AS DOUBLE), 0) > 0 AND COALESCE(TRY_CAST(PCTUSED AS DOUBLE), 0) < 50 THEN 1 ELSE 0 END) AS underloaded_buckets,
                    COALESCE(SUM(TRY_CAST(AVAILCAP AS DOUBLE)), 0) AS total_avail_cap,
                    COALESCE(SUM(TRY_CAST(MPTOTLOAD AS DOUBLE)), 0) AS total_load
                FROM "{self.RES_PROJ_TABLE}"
                {where_sql}
                """,
                params,
            ) or {}
            distinct_res = int(agg.get("distinct_res") or 0)
            avg_util = float(agg.get("avg_util") or 0)
            overloaded_buckets = int(agg.get("overloaded_buckets") or 0)
            underloaded_buckets = int(agg.get("underloaded_buckets") or 0)

            top_rows = self.data.query(
                f"""
                SELECT
                    TRIM(RES) AS resource,
                    TRIM(LOC) AS location,
                    ROUND(COALESCE(AVG(TRY_CAST(PCTUSED AS DOUBLE)), 0), 1) AS avg_pct_used,
                    ROUND(COALESCE(MAX(TRY_CAST(PCTUSED AS DOUBLE)), 0), 1) AS peak_pct_used,
                    ROUND(COALESCE(SUM(TRY_CAST(MPTOTLOAD AS DOUBLE)), 0), 1) AS total_load
                FROM "{self.RES_PROJ_TABLE}"
                {where_sql}
                GROUP BY 1, 2
                ORDER BY peak_pct_used DESC, avg_pct_used DESC
                LIMIT 8
                """,
                params,
            )
            if top_rows:
                top_str = "; ".join(
                    f"`{r['resource']}` @ `{r['location']}` (peak {r['peak_pct_used']}%, avg {r['avg_pct_used']}%)"
                    for r in top_rows[:5]
                )
                findings.append(f"Highest-utilized resources: {top_str}.")
            findings.append(
                f"Bucket breakdown: **{_fmt_num(overloaded_buckets)}** high/overloaded (>=95%) buckets, **{_fmt_num(underloaded_buckets)}** underloaded (<50%) buckets."
            )

        if self.data.has_table(self.RES_EXC_TABLE):
            evidence.append(self.RES_EXC_TABLE)
            emeta = self.data.table_meta(self.RES_EXC_TABLE)
            ewhere, eparams = _build_scope_filters(ctx, emeta.columns if emeta else ())
            if ctx.resource:
                conj = " AND " if ewhere else "WHERE "
                ewhere += f"{conj}UPPER(TRIM(RES)) = UPPER(TRIM(?))"
                eparams.append(ctx.resource)
            for row in self.data.query(
                f"""
                SELECT
                    TRIM(RES) AS resource,
                    COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'Capacity Exception') AS reason,
                    COUNT(*) AS occurrences
                FROM "{self.RES_EXC_TABLE}"
                {ewhere}
                GROUP BY 1, 2
                ORDER BY occurrences DESC
                LIMIT 5
                """,
                eparams,
            ):
                root_causes.append(
                    f"`{row['resource']}`: **{row['reason']}** ({_fmt_num(row['occurrences'])} occurrences)"
                )

        scope_title = f"Resource `{ctx.resource}`" if ctx.resource else f"All resources ({_fmt_num(distinct_res)} distinct resources)"
        summary = (
            f"**Capacity & Utilization — {scope_title}:** Average Utilization **{avg_util:.1f}%** "
            f"(High/Overloaded buckets: **{_fmt_num(overloaded_buckets)}**, Underloaded buckets: **{_fmt_num(underloaded_buckets)}**)."
        )

        return AgentResponse(
            agent="CapacityAgent",
            workflow="Resource Capacity & Utilization (V2)",
            summary=summary,
            confirmed_findings=findings,
            root_causes=root_causes,
            metrics={
                "distinct_resources": distinct_res,
                "avg_utilization_pct": avg_util,
                "overloaded_buckets": overloaded_buckets,
                "underloaded_buckets": underloaded_buckets,
            },
            rows=top_rows,
            evidence_sources=evidence,
            confidence="High" if evidence else "Low",
            recommended_next_checks=[
                "Check if demands are met for all order point items",
                "How does the inventory position look across all items?",
                "Run master data validation check",
            ],
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
