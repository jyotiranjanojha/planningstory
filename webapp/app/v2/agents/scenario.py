"""DuckDB-powered Scenario Comparison Agent (IFSP V2)."""

from __future__ import annotations

import time
from typing import List

from ..core.data import PlanningDataEngine
from .base import AgentContext, AgentResponse


class ScenarioAgent:
    """Fast DuckDB specialist for comparing planning scenarios and ranking KPI deltas."""

    DEMAND_TABLE = "by_if_snop_out_inddmdview"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def run(self, ctx: AgentContext) -> AgentResponse:
        t0 = time.perf_counter()
        if not self.data.has_table(self.DEMAND_TABLE):
            return AgentResponse(
                agent="ScenarioAgent",
                workflow="Scenario Comparison (V2)",
                summary="Demand output snapshot is unavailable for scenario comparison.",
                confidence="Low",
                data_gaps=[f"Missing {self.DEMAND_TABLE}"],
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        rows = self.data.query(
            f"""
            SELECT
                COALESCE(NULLIF(TRIM(CAPTURE_WK), ''), 'UNKNOWN_WK') AS capture_wk,
                COALESCE(NULLIF(TRIM(SIMULATION_NAME), ''), 'DEFAULT') AS scenario,
                COUNT(*) AS order_lines,
                ROUND(COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0), 2) AS demand_qty,
                ROUND(COALESCE(SUM(TRY_CAST(SCHEDQTY AS DOUBLE)), 0), 2) AS scheduled_qty,
                ROUND(COALESCE(SUM(GREATEST(TRY_CAST(QTY AS DOUBLE) - COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0), 0)), 0), 2) AS unmet_qty
            FROM "{self.DEMAND_TABLE}"
            GROUP BY 1, 2
            ORDER BY 1 DESC, 2 ASC
            LIMIT 10
            """
        )

        if not rows:
            return AgentResponse(
                agent="ScenarioAgent",
                workflow="Scenario Comparison (V2)",
                summary="No scenario rows found in the current snapshot.",
                evidence_sources=[self.DEMAND_TABLE],
                confidence="Low",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        findings: List[str] = []
        data_gaps: List[str] = []
        for r in rows:
            dmd = float(r.get("demand_qty") or 0)
            sch = float(r.get("scheduled_qty") or 0)
            fr = (sch / dmd * 100.0) if dmd > 0 else 0.0
            r["fill_rate_pct"] = round(fr, 2)
            findings.append(
                f"Week `{r['capture_wk']}` / Scenario `{r['scenario']}`: "
                f"Fill Rate **{fr:.1f}%** (Demand {dmd:,.0f}, Scheduled {sch:,.0f}, Unmet {float(r['unmet_qty'] or 0):,.0f})."
            )

        if len(rows) < 2:
            data_gaps.append("Only 1 scenario/week is present in the loaded snapshot; upload or connect a second scenario to compute strict deltas.")
            summary = f"**Single Scenario Available:** `{rows[0]['scenario']}` (Week `{rows[0]['capture_wk']}`) — Fill Rate **{rows[0]['fill_rate_pct']:.1f}%**."
        else:
            base, comp = rows[0], rows[1]
            delta_fr = comp["fill_rate_pct"] - base["fill_rate_pct"]
            delta_unmet = float(comp["unmet_qty"] or 0) - float(base["unmet_qty"] or 0)
            summary = (
                f"**Scenario Delta (`{comp['scenario']}` vs `{base['scenario']}`):** "
                f"Fill Rate **{delta_fr:+.2f} pp**, Unmet Qty **{delta_unmet:+,.0f}**."
            )

        return AgentResponse(
            agent="ScenarioAgent",
            workflow="Scenario Comparison (V2)",
            summary=summary,
            confirmed_findings=findings,
            metrics={"scenarios_found": len(rows)},
            rows=rows,
            evidence_sources=[self.DEMAND_TABLE],
            confidence="High" if len(rows) >= 2 else "Medium",
            data_gaps=data_gaps,
            recommended_next_checks=["Filter by a specific item or site to isolate scenario trade-offs."],
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
