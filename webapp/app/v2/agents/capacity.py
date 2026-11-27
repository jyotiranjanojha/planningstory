"""DuckDB-powered Resource Capacity & Utilization Agent (IFSP V2).

Question-aware specialist supporting:
1. Bottleneck / overloaded resource diagnosis
2. Resource-to-item demand impact analysis ("which items are impacted by this resource?")
3. Underutilized / spare capacity analysis
4. Capacity exception breakdown
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from ..core.data import PlanningDataEngine
from .base import AgentContext, AgentResponse
from .fulfillment import _build_scope_filters, _fmt_num


class CapacityAgent:
    """Fast DuckDB specialist for resource utilization, bottlenecks, and demand impact."""

    RES_PROJ_TABLE = "by_if_snop_out_resprojstatic"
    RES_EXC_TABLE = "by_if_snop_out_resexception"
    RES_LINK_TABLE = "by_if_snop_out_resloadinddmdlink"
    DEMAND_TABLE = "by_if_snop_out_inddmdview"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def _run_impacted_items(self, ctx: AgentContext, t0: float) -> AgentResponse:
        evidence: List[str] = []
        findings: List[str] = []
        root_causes: List[str] = []
        rows: List[Dict[str, Any]] = []

        if not self.data.has_table(self.RES_LINK_TABLE):
            return AgentResponse(
                agent="CapacityAgent",
                workflow="Resource-to-Item Impact (V2)",
                summary="Resource-to-demand link table (`by_if_snop_out_resloadinddmdlink`) is unavailable in this snapshot.",
                confidence="Low",
                data_gaps=[f"Missing {self.RES_LINK_TABLE}"],
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        evidence.append(self.RES_LINK_TABLE)
        has_dmd = self.data.has_table(self.DEMAND_TABLE)
        if has_dmd:
            evidence.append(self.DEMAND_TABLE)

        # Scope to explicit resource if provided; otherwise all bottleneck/exception resources
        params: List[Any] = []
        if ctx.resource:
            res_filter_sql = "WHERE UPPER(TRIM(r.RES)) = UPPER(TRIM(?))"
            params.append(ctx.resource)
            scope_label = f"Resource `{ctx.resource}`"
        else:
            bottleneck_clauses: List[str] = []
            if self.data.has_table(self.RES_EXC_TABLE):
                evidence.append(self.RES_EXC_TABLE)
                bottleneck_clauses.append(
                    f'UPPER(TRIM(r.RES)) IN (SELECT DISTINCT UPPER(TRIM(RES)) FROM "{self.RES_EXC_TABLE}" WHERE TRIM(COALESCE(RES, \'\')) <> \'\')'
                )
            if self.data.has_table(self.RES_PROJ_TABLE):
                evidence.append(self.RES_PROJ_TABLE)
                bottleneck_clauses.append(
                    f'UPPER(TRIM(r.RES)) IN (SELECT DISTINCT UPPER(TRIM(RES)) FROM "{self.RES_PROJ_TABLE}" WHERE COALESCE(TRY_CAST(PCTUSED AS DOUBLE), 0) >= 95)'
                )
            res_filter_sql = f"WHERE ({' OR '.join(bottleneck_clauses)})" if bottleneck_clauses else "WHERE TRIM(COALESCE(r.RES, '')) <> ''"
            scope_label = "Bottleneck / Overloaded Resources"

        if has_dmd:
            rows = self.data.query(
                f"""
                WITH dmd_agg AS (
                    SELECT
                        UPPER(TRIM(ITEM)) AS item_key,
                        ROUND(COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0), 2) AS demand_qty,
                        ROUND(COALESCE(SUM(TRY_CAST(SCHEDQTY AS DOUBLE)), 0), 2) AS sched_qty,
                        ROUND(COALESCE(SUM(GREATEST(TRY_CAST(QTY AS DOUBLE) - COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0), 0)), 0), 2) AS unmet_qty
                    FROM "{self.DEMAND_TABLE}"
                    GROUP BY 1
                )
                SELECT
                    TRIM(r.DMDITEM) AS item,
                    TRIM(r.RES) AS resource,
                    TRIM(r.RESLOC) AS location,
                    COUNT(*) AS peg_links,
                    COALESCE(MAX(d.demand_qty), 0) AS demand_qty,
                    COALESCE(MAX(d.sched_qty), 0) AS sched_qty,
                    COALESCE(MAX(d.unmet_qty), 0) AS unmet_qty
                FROM "{self.RES_LINK_TABLE}" r
                LEFT JOIN dmd_agg d ON UPPER(TRIM(r.DMDITEM)) = d.item_key
                {res_filter_sql}
                  AND TRIM(COALESCE(r.DMDITEM, '')) <> ''
                GROUP BY 1, 2, 3
                ORDER BY unmet_qty DESC, peg_links DESC
                LIMIT 12
                """,
                params,
            )
        else:
            rows = self.data.query(
                f"""
                SELECT
                    TRIM(r.DMDITEM) AS item,
                    TRIM(r.RES) AS resource,
                    TRIM(r.RESLOC) AS location,
                    COUNT(*) AS peg_links,
                    0 AS demand_qty,
                    0 AS sched_qty,
                    0 AS unmet_qty
                FROM "{self.RES_LINK_TABLE}" r
                {res_filter_sql}
                  AND TRIM(COALESCE(r.DMDITEM, '')) <> ''
                GROUP BY 1, 2, 3
                ORDER BY peg_links DESC
                LIMIT 12
                """,
                params,
            )

        distinct_items = sorted({r["item"] for r in rows if r.get("item")})
        distinct_res = sorted({r["resource"] for r in rows if r.get("resource")})
        total_unmet = sum(float(r.get("unmet_qty") or 0) for r in rows)

        if rows:
            for r in rows[:8]:
                dmd = float(r.get("demand_qty") or 0)
                unmet = float(r.get("unmet_qty") or 0)
                fill = ((dmd - unmet) / dmd * 100.0) if dmd > 0 else 0.0
                findings.append(
                    f"Item `{r['item']}` via `{r['resource']}` @ `{r['location']}` — "
                    f"Demand: **{_fmt_num(dmd)}**, Unmet: **{_fmt_num(unmet)}** ({fill:.1f}% fill, {r['peg_links']} peg links)"
                )
            summary = (
                f"**Impacted Demand Items — {scope_label}:** Found **{len(distinct_items)} impacted item(s)** "
                f"linked across **{len(distinct_res)} bottleneck resource(s)** "
                f"(combined unmet demand: **{_fmt_num(total_unmet)}**)."
            )
        else:
            summary = (
                f"**Impacted Demand Items — {scope_label}:** No direct demand-peg links found in `{self.RES_LINK_TABLE}` "
                f"for this resource filter."
            )
            findings.append("Try checking all bottleneck resources or inspect resource exceptions directly.")

        # Include capacity exceptions for those resources
        if self.data.has_table(self.RES_EXC_TABLE) and distinct_res:
            ph = ", ".join(["UPPER(TRIM(?))"] * len(distinct_res))
            for erow in self.data.query(
                f"""
                SELECT
                    TRIM(RES) AS resource,
                    COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'Capacity Exception') AS reason,
                    COUNT(*) AS occurrences
                FROM "{self.RES_EXC_TABLE}"
                WHERE UPPER(TRIM(RES)) IN ({ph})
                GROUP BY 1, 2
                ORDER BY occurrences DESC
                LIMIT 5
                """,
                distinct_res,
            ):
                root_causes.append(
                    f"`{erow['resource']}`: **{erow['reason']}** ({_fmt_num(erow['occurrences'])} occurrences)"
                )

        top_item = distinct_items[0] if distinct_items else "2000-280-868"
        return AgentResponse(
            agent="CapacityAgent",
            workflow="Resource-to-Item Impact (V2)",
            summary=summary,
            confirmed_findings=findings,
            root_causes=root_causes,
            metrics={
                "impacted_items": len(distinct_items),
                "bottleneck_resources": len(distinct_res),
                "combined_unmet_qty": round(total_unmet, 2),
                "top_resource": distinct_res[0] if distinct_res else None,
            },
            rows=rows,
            evidence_sources=evidence,
            confidence="High" if rows else "Medium",
            recommended_next_checks=[
                f"Why is demand unmet for item {top_item}?",
                "Which resources are underutilized with spare capacity?",
                "Run master data validation check",
            ],
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    def _run_underutilized(self, ctx: AgentContext, t0: float) -> AgentResponse:
        evidence: List[str] = []
        findings: List[str] = []
        rows: List[Dict[str, Any]] = []

        if self.data.has_table(self.RES_PROJ_TABLE):
            evidence.append(self.RES_PROJ_TABLE)
            meta = self.data.table_meta(self.RES_PROJ_TABLE)
            where_sql, params = _build_scope_filters(ctx, meta.columns if meta else ())
            rows = self.data.query(
                f"""
                SELECT
                    TRIM(RES) AS resource,
                    TRIM(LOC) AS location,
                    ROUND(COALESCE(AVG(TRY_CAST(PCTUSED AS DOUBLE)), 0), 1) AS avg_pct_used,
                    ROUND(COALESCE(MAX(TRY_CAST(PCTUSED AS DOUBLE)), 0), 1) AS peak_pct_used,
                    ROUND(COALESCE(SUM(GREATEST(TRY_CAST(AVAILCAP AS DOUBLE) - COALESCE(TRY_CAST(MPTOTLOAD AS DOUBLE), 0), 0)), 0), 1) AS spare_cap
                FROM "{self.RES_PROJ_TABLE}"
                {where_sql}
                GROUP BY 1, 2
                HAVING COALESCE(AVG(TRY_CAST(PCTUSED AS DOUBLE)), 0) < 50
                ORDER BY spare_cap DESC, avg_pct_used ASC
                LIMIT 8
                """,
                params,
            )
            for r in rows[:6]:
                findings.append(
                    f"`{r['resource']}` @ `{r['location']}` — avg **{r['avg_pct_used']}%** (peak {r['peak_pct_used']}%), spare capacity: **{_fmt_num(r['spare_cap'])}**"
                )

        summary = (
            f"**Underutilized & Spare Capacity:** Found **{len(rows)}** underutilized resource-location pairs (<50% avg utilization) ranked by available spare capacity."
        )
        return AgentResponse(
            agent="CapacityAgent",
            workflow="Underutilized Capacity Analysis (V2)",
            summary=summary,
            confirmed_findings=findings or ["No underutilized resources (<50%) found in current scope."],
            metrics={"underutilized_resources": len(rows)},
            rows=rows,
            evidence_sources=evidence,
            confidence="High" if evidence else "Low",
            recommended_next_checks=[
                "Which resources have capacity bottlenecks?",
                "Which items are impacted by bottleneck resources?",
                "Check if demands are met for all order point items",
            ],
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    def run(self, ctx: AgentContext) -> AgentResponse:
        t0 = time.perf_counter()
        ql = (ctx.question or "").lower()

        # Mode 1: Resource -> Impacted Items question (e.g. "this resource are impacting which item")
        if any(k in ql for k in ("impact", "affect", "which item", "what item", "which product", "which sku", "demand linked", "linked item")):
            return self._run_impacted_items(ctx, t0)

        # Mode 2: Underutilized / Spare capacity question
        if any(k in ql for k in ("underutil", "underload", "idle", "spare capacity", "excess capacity", "low util")):
            return self._run_underutilized(ctx, t0)

        # Mode 3/4: Bottleneck, Utilization & Capacity Exceptions Overview
        evidence: List[str] = []
        findings: List[str] = []
        root_causes: List[str] = []
        top_rows: List[Dict[str, Any]] = []

        distinct_res = 0
        avg_util = 0.0
        overloaded_buckets = 0
        underloaded_buckets = 0
        total_avail_cap = 0.0
        total_load = 0.0

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
            total_avail_cap = float(agg.get("total_avail_cap") or 0)
            total_load = float(agg.get("total_load") or 0)
            weighted_util = (total_load / total_avail_cap * 100.0) if total_avail_cap > 0 else 0.0

            findings.append(
                f"Total Capacity Load vs Available: **{_fmt_num(total_load)}** load / **{_fmt_num(total_avail_cap)}** available "
                f"(Weighted Load/Avail: **{weighted_util:.1f}%**, Simple Bucket Avg: **{avg_util:.1f}%**)."
            )

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

        top_res_name = top_rows[0]["resource"] if top_rows else None
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
                "total_load": round(total_load, 2),
                "total_avail_cap": round(total_avail_cap, 2),
                "weighted_utilization_pct": round((total_load / total_avail_cap * 100.0) if total_avail_cap > 0 else 0.0, 2),
                "overloaded_buckets": overloaded_buckets,
                "underloaded_buckets": underloaded_buckets,
                "top_resource": top_res_name,
            },
            rows=top_rows,
            evidence_sources=evidence,
            confidence="High" if evidence else "Low",
            recommended_next_checks=[
                "Which items are impacted by these bottleneck resources?",
                "Which resources are underutilized with spare capacity?",
                "Check if demands are met for all order point items",
            ],
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
