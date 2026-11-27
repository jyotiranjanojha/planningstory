"""DuckDB-powered Root Cause Analyzer Agent (IFSP V2).

Answers all 10 planner root-cause question types in <150ms using live BY ESP
DuckDB views, returning the exact stats + tailored narrative + raw_data contract.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..core.data import PlanningDataEngine
from .fulfillment import _fmt_num

QUESTION_LABELS: Dict[str, str] = {
    "full_diagnosis": "Full Demand-Supply Diagnosis",
    "why_unmet": "Why is demand unmet?",
    "why_late": "Why did demand get late?",
    "why_short": "Why did demand get short?",
    "why_early": "Why was demand met early?",
    "capacity_constraints": "Resource / Capacity Constraints",
    "bom_sourcing_gaps": "BOM and Sourcing Gaps",
    "priority_conflict": "Priority Conflict Impact",
    "supply_pegging": "Supply Pegging & Lineage",
    "eoh": "End-of-Horizon (EOH) Inventory",
}


def _sf(val: Any) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


class RootCauseAgent:
    """Fast DuckDB specialist for item-level root cause diagnosis across all 10 question types."""

    DEMAND_TABLE = "by_if_snop_out_inddmdview"
    LINK_TABLE = "by_if_snop_out_inddmdlink"
    RES_LINK_TABLE = "by_if_snop_out_resloadinddmdlink"
    SKU_EXC_TABLE = "by_if_snop_out_skuexception"
    RES_EXC_TABLE = "by_if_snop_out_resexception"
    SKUSTAT_TABLE = "by_if_snop_out_skustatstatic"
    INV_TABLE = "if_snop_inventory"
    BOM_TABLE = "if_snop_billofmaterials"
    SOURCING_TABLE = "if_snop_sourcing"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def _where(
        self,
        table: str,
        item_col: str,
        loc_col: Optional[str],
        item_id: str,
        week_id: Optional[str],
        scenario_id: Optional[str],
        site: Optional[str],
    ) -> tuple[str, List[Any]]:
        meta = self.data.table_meta(table)
        cols = {c.upper() for c in (meta.columns if meta else ())}
        clauses = [f"UPPER(TRIM({item_col})) = UPPER(TRIM(?))"]
        params: List[Any] = [item_id]
        if site and loc_col and loc_col.upper() in cols:
            clauses.append(f"UPPER(TRIM({loc_col})) = UPPER(TRIM(?))")
            params.append(site)
        if week_id and "CAPTURE_WK" in cols:
            clauses.append("TRIM(CAPTURE_WK) = TRIM(?)")
            params.append(week_id)
        if scenario_id and "SIMULATION_NAME" in cols:
            clauses.append("UPPER(TRIM(SIMULATION_NAME)) = UPPER(TRIM(?))")
            params.append(scenario_id)
        return "WHERE " + " AND ".join(clauses), params

    def analyze(
        self,
        demand_id: Optional[str],
        week_id: Optional[str] = None,
        scenario_id: Optional[str] = None,
        site: Optional[str] = None,
        question_type: str = "full_diagnosis",
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        item_id = (demand_id or "").strip()
        qtype = (question_type or "full_diagnosis").strip().lower()
        qlabel = QUESTION_LABELS.get(qtype, qtype.replace("_", " ").title())

        if not item_id:
            return {
                "question_type": qtype,
                "question_label": qlabel,
                "llm_used": False,
                "stats": {},
                "narrative": "Please enter or pick an **Item / Product ID** to run Root Cause Analysis.",
                "raw_data": {},
            }

        if not self.data.has_table(self.DEMAND_TABLE):
            return {
                "question_type": qtype,
                "question_label": qlabel,
                "llm_used": False,
                "stats": {"item": item_id},
                "narrative": f"Demand output table (`{self.DEMAND_TABLE}`) is unavailable.",
                "raw_data": {},
            }

        # 1. Core demand & timing stats
        w_dmd, p_dmd = self._where(self.DEMAND_TABLE, "ITEM", "LOC", item_id, week_id, scenario_id, site)
        dmd = self.data.query_one(
            f"""
            SELECT
                COUNT(*) AS lines,
                ANY_VALUE(TRIM(CAPTURE_WK)) AS resolved_week,
                ANY_VALUE(TRIM(SIMULATION_NAME)) AS resolved_scenario,
                COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0) AS demand_qty,
                COALESCE(SUM(TRY_CAST(SCHEDQTY AS DOUBLE)), 0) AS scheduled_qty,
                COALESCE(SUM(GREATEST(TRY_CAST(QTY AS DOUBLE) - COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0), 0)), 0) AS unmet_qty,
                COALESCE(SUM(CASE WHEN SCHEDDATE IS NOT NULL AND NEEDDATE IS NOT NULL AND TRIM(SCHEDDATE) <> '' AND TRIM(NEEDDATE) <> '' AND TRY_CAST(SCHEDDATE AS DATE) > TRY_CAST(NEEDDATE AS DATE) THEN COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0) ELSE 0 END), 0) AS late_qty,
                COALESCE(SUM(CASE WHEN SCHEDDATE IS NOT NULL AND NEEDDATE IS NOT NULL AND TRIM(SCHEDDATE) <> '' AND TRIM(NEEDDATE) <> '' AND TRY_CAST(SCHEDDATE AS DATE) < TRY_CAST(NEEDDATE AS DATE) THEN COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0) ELSE 0 END), 0) AS early_qty,
                COUNT(DISTINCT PRIORITY) AS priority_levels,
                MIN(TRY_CAST(PRIORITY AS DOUBLE)) AS min_priority,
                MAX(TRY_CAST(PRIORITY AS DOUBLE)) AS max_priority
            FROM "{self.DEMAND_TABLE}"
            {w_dmd}
            """,
            p_dmd,
        ) or {}

        demand_qty = _sf(dmd.get("demand_qty"))
        sched_qty = _sf(dmd.get("scheduled_qty"))
        unmet_qty = _sf(dmd.get("unmet_qty"))
        late_qty = _sf(dmd.get("late_qty"))
        early_qty = _sf(dmd.get("early_qty"))
        fill_rate = round((sched_qty / demand_qty * 100.0) if demand_qty > 0 else 0.0, 2)
        if demand_qty <= 0:
            meet_status = "no_demand"
        elif unmet_qty <= 0 and late_qty > 0:
            meet_status = "met_late"
        elif unmet_qty <= 0:
            meet_status = "met"
        elif sched_qty <= 0:
            meet_status = "not_met"
        else:
            meet_status = "partially_met"

        # 2. SKU exceptions
        sku_excs: List[Dict[str, Any]] = []
        if self.data.has_table(self.SKU_EXC_TABLE):
            w_exc, p_exc = self._where(self.SKU_EXC_TABLE, "ITEM", "LOC", item_id, week_id, scenario_id, site)
            sku_excs = self.data.query(
                f"""
                SELECT
                    COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'SKU Exception') AS reason,
                    COALESCE(NULLIF(TRIM(SEVERITY), ''), 'Info') AS severity,
                    COUNT(*) AS occurrences
                FROM "{self.SKU_EXC_TABLE}"
                {w_exc}
                GROUP BY 1, 2
                ORDER BY occurrences DESC
                LIMIT 8
                """,
                p_exc,
            )

        # 3. Resource load & capacity exceptions
        res_rows: List[Dict[str, Any]] = []
        res_excs: List[Dict[str, Any]] = []
        affected_resources: List[str] = []
        if self.data.has_table(self.RES_LINK_TABLE):
            w_rl, p_rl = self._where(self.RES_LINK_TABLE, "DMDITEM", "DMDLOC", item_id, week_id, scenario_id, site)
            res_rows = self.data.query(
                f"""
                SELECT
                    TRIM(RES) AS res,
                    TRIM(SUPPLYMETHOD) AS method,
                    COUNT(*) AS links
                FROM "{self.RES_LINK_TABLE}"
                {w_rl}
                AND TRIM(COALESCE(RES, '')) <> ''
                GROUP BY 1, 2
                ORDER BY links DESC
                LIMIT 10
                """,
                p_rl,
            )
            affected_resources = sorted({r["res"] for r in res_rows if r.get("res")})

        if self.data.has_table(self.RES_EXC_TABLE) and affected_resources:
            ph = ", ".join(["UPPER(TRIM(?))"] * len(affected_resources))
            res_excs = self.data.query(
                f"""
                SELECT
                    TRIM(RES) AS res,
                    COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'Capacity Exception') AS reason,
                    COUNT(*) AS occurrences
                FROM "{self.RES_EXC_TABLE}"
                WHERE UPPER(TRIM(RES)) IN ({ph})
                GROUP BY 1, 2
                ORDER BY occurrences DESC
                LIMIT 8
                """,
                affected_resources,
            )

        # 4. Supply pegging & competing demand
        pegging_rows: List[Dict[str, Any]] = []
        competing_count = 0
        if self.data.has_table(self.LINK_TABLE):
            w_l, p_l = self._where(self.LINK_TABLE, "DMDITEM", "DMDLOC", item_id, week_id, scenario_id, site)
            pegging_rows = self.data.query(
                f"""
                SELECT
                    TRIM(SUPPLYITEM) AS supply_item,
                    TRIM(SUPPLYLOC) AS supply_loc,
                    TRIM(SUPPLYTYPE) AS supply_type,
                    TRIM(SUPPLYMETHOD) AS supply_method,
                    ROUND(COALESCE(SUM(TRY_CAST(SUPPLYPEGQTY AS DOUBLE)), 0), 2) AS pegged_qty
                FROM "{self.LINK_TABLE}"
                {w_l}
                GROUP BY 1, 2, 3, 4
                ORDER BY pegged_qty DESC
                LIMIT 8
                """,
                p_l,
            )
            supply_items = [r["supply_item"] for r in pegging_rows if r.get("supply_item")]
            if supply_items:
                ph = ", ".join(["UPPER(TRIM(?))"] * len(supply_items))
                comp = self.data.query_one(
                    f"""
                    SELECT COUNT(*) AS c
                    FROM "{self.LINK_TABLE}"
                    WHERE UPPER(TRIM(SUPPLYITEM)) IN ({ph}) AND UPPER(TRIM(DMDITEM)) <> UPPER(TRIM(?))
                    """,
                    supply_items + [item_id],
                ) or {}
                competing_count = int(comp.get("c") or 0)

        # 5. Inventory / EOH & BOM/Sourcing counts
        oh_qty = 0.0
        if self.data.has_table(self.INV_TABLE):
            w_i, p_i = self._where(self.INV_TABLE, "ITEM", "LOC", item_id, None, None, site)
            inv = self.data.query_one(f'SELECT COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0) AS q FROM "{self.INV_TABLE}" {w_i}', p_i) or {}
            oh_qty = _sf(inv.get("q"))

        eoh_row: Dict[str, Any] = {}
        if self.data.has_table(self.SKUSTAT_TABLE):
            w_s, p_s = self._where(self.SKUSTAT_TABLE, "ITEM", "LOC", item_id, week_id, scenario_id, site)
            eoh_row = self.data.query_one(
                f"""
                SELECT
                    ROUND(COALESCE(MAX(TRY_CAST(MAXCONSTRPROJOH AS DOUBLE)), 0), 2) AS max_proj_oh,
                    ROUND(COALESCE(SUM(TRY_CAST(CONSTRSTOCKOUTQTY AS DOUBLE)), 0), 2) AS stockout_qty,
                    ROUND(COALESCE(SUM(TRY_CAST(CONSTRSTOCKLOWQTY AS DOUBLE)), 0), 2) AS stocklow_qty
                FROM "{self.SKUSTAT_TABLE}"
                {w_s}
                """,
                p_s,
            ) or {}

        bom_links = 0
        if self.data.has_table(self.BOM_TABLE):
            bom_links = int((self.data.query_one(f'SELECT COUNT(*) AS c FROM "{self.BOM_TABLE}" WHERE UPPER(TRIM(ITEM)) = UPPER(TRIM(?))', [item_id]) or {}).get("c") or 0)
        sourcing_rules = 0
        if self.data.has_table(self.SOURCING_TABLE):
            sourcing_rules = int((self.data.query_one(f'SELECT COUNT(*) AS c FROM "{self.SOURCING_TABLE}" WHERE UPPER(TRIM(ITEM)) = UPPER(TRIM(?))', [item_id]) or {}).get("c") or 0)

        cap_exc_total = sum(int(r.get("occurrences") or 0) for r in res_excs)
        duration_ms = int((time.perf_counter() - t0) * 1000)

        # ── 6. Build Business-Focused 3-Pillar Executive Summary ─────────────
        # (1) What is the problem
        problem_bullets: List[str] = []
        if demand_qty <= 0:
            problem_headline = f"No independent demand is recorded for item `{item_id}` in the selected scope."
            problem_bullets.append("0 demand units found in `by_if_snop_out_inddmdview` for the current item/week/scenario filter.")
        elif unmet_qty > 0:
            problem_headline = (
                f"Item `{item_id}` has {_fmt_num(unmet_qty)} unmet units out of {_fmt_num(demand_qty)} total demand "
                f"({fill_rate:.1f}% fill rate; status: {meet_status.replace('_', ' ')})."
            )
            problem_bullets.append(
                f"Scheduled supply covers **{_fmt_num(sched_qty)}** units, leaving a shortfall of **{_fmt_num(unmet_qty)}** units."
            )
        elif late_qty > 0:
            problem_headline = (
                f"Item `{item_id}` demand ({_fmt_num(demand_qty)} units) is fully scheduled but {_fmt_num(late_qty)} units are delivered late."
            )
        else:
            problem_headline = (
                f"Item `{item_id}` demand ({_fmt_num(demand_qty)} units) is fully met on time (100.0% fill rate)."
            )

        if late_qty > 0:
            problem_bullets.append(f"**{_fmt_num(late_qty)}** scheduled units are **late** (scheduled after need date).")
        if early_qty > 0 and qtype in {"why_early", "why_late", "full_diagnosis"}:
            problem_bullets.append(f"**{_fmt_num(early_qty)}** scheduled units are **early** (scheduled before need date).")
        if qtype == "eoh" or _sf(eoh_row.get("stockout_qty")) > 0:
            problem_bullets.append(
                f"On-hand inventory: **{_fmt_num(oh_qty)}** | Max Constrained Proj OH: **{_fmt_num(eoh_row.get('max_proj_oh', 0))}** | "
                f"Stockout Qty: **{_fmt_num(eoh_row.get('stockout_qty', 0))}** | Below Safety Stock: **{_fmt_num(eoh_row.get('stocklow_qty', 0))}**."
            )
        if qtype == "supply_pegging" and pegging_rows:
            top_peg = "; ".join(
                f"`{p['supply_item']}` @ `{p['supply_loc']}` ({_fmt_num(p['pegged_qty'])})"
                for p in pegging_rows[:4]
            )
            problem_bullets.append(f"Primary pegged supply sources: {top_peg}.")

        # (2) Why the problem happened
        why_bullets: List[str] = []
        for e in sku_excs[:5]:
            why_bullets.append(
                f"SKU Exception — **{e['reason']}** ({_fmt_num(e['occurrences'])} occurrences, severity: {e['severity']})."
            )
        for r in res_excs[:5]:
            why_bullets.append(
                f"Capacity Constraint on resource `{r['res']}` — **{r['reason']}** ({_fmt_num(r['occurrences'])} occurrences)."
            )
        if competing_count > 0:
            why_bullets.append(
                f"Upstream supply contention: **{_fmt_num(competing_count)}** competing demand pegging rows share upstream supply with `{item_id}` "
                f"(priority tiers: {int(dmd.get('priority_levels') or 0)}, range {dmd.get('min_priority')}–{dmd.get('max_priority')})."
            )
        if bom_links == 0 and sourcing_rules == 0 and unmet_qty > 0:
            why_bullets.append(
                f"Master data gap: `{item_id}` has **0 BOM links** and **0 sourcing rules** in `if_snop_billofmaterials` / `if_snop_sourcing`."
            )
        elif qtype == "bom_sourcing_gaps":
            why_bullets.append(
                f"Master data footprint: **{bom_links}** BOM component link(s) and **{sourcing_rules}** sourcing rule(s) defined."
            )
        elif qtype == "capacity_constraints" and not res_excs:
            why_bullets.append(
                f"Linked resources: {', '.join(f'`{r}`' for r in affected_resources[:6]) or 'None found'} (0 capacity exceptions logged)."
            )

        if why_bullets:
            why_headline = f"Identified {len(why_bullets)} confirmed driver(s) across BY ESP exception, capacity, and pegging tables."
        elif unmet_qty > 0 or late_qty > 0:
            why_headline = f"No direct SKU or resource exceptions were logged on `{item_id}`; constraint likely sits upstream."
            why_bullets.append(
                f"Inspect upstream BOM components ({bom_links} links) and sourcing rules ({sourcing_rules}) for hidden shortages."
            )
        else:
            why_headline = "No blocking supply, capacity, or master-data exceptions detected."
            why_bullets.append("Supply, capacity, and lead times are sufficient to satisfy all demand in this scope.")

        # (3) Possible options to fix the problem (evidence-driven)
        fix_options: List[Dict[str, str]] = []
        sku_reasons_lower = " ".join(str(e.get("reason") or "").lower() for e in sku_excs)

        if res_excs:
            top_res = ", ".join(f"`{r['res']}`" for r in res_excs[:3])
            fix_options.append({
                "action": f"Relieve bottleneck capacity on {top_res}",
                "detail": "Evaluate extra shifts/calendar capacity, alternate production methods/routings, or load balancing across qualified sites.",
                "impact": "High",
                "owner": "Capacity Planning",
            })

        if any(k in sku_reasons_lower for k in ("bom", "parent sku", "production method")):
            fix_options.append({
                "action": "Correct invalid BOM / production method master data",
                "detail": "Fix flagged BOM numbers, parent SKU mappings, or routing definitions in `if_snop_billofmaterials` / `if_snop_productionmethod` and re-solve.",
                "impact": "High",
                "owner": "Master Data",
            })

        if "material" in sku_reasons_lower:
            fix_options.append({
                "action": "Expedite constrained upstream material / components",
                "detail": "Pull in open purchase orders (`planpurch` / `schedrcpts`), qualify an alternate BOM/substitute component, or reallocate component stock.",
                "impact": "High",
                "owner": "Materials / Sourcing",
            })

        if "lead time" in sku_reasons_lower:
            fix_options.append({
                "action": "Compress or expedite production / sourcing lead times",
                "detail": "Review lead-time parameters in `if_snop_sourcing` / `if_snop_productionmethod`, split lots, or use expedited transport to pull in receipt dates.",
                "impact": "Medium",
                "owner": "Supply Planning",
            })

        if competing_count > 0 and (unmet_qty > 0 or late_qty > 0):
            fix_options.append({
                "action": "Re-prioritize demand or rebalance shared supply allocation",
                "detail": f"Review demand priority rules against the {_fmt_num(competing_count)} competing pegging rows sharing upstream supply with `{item_id}`.",
                "impact": "Medium",
                "owner": "Demand / Supply Planning",
            })

        if (bom_links == 0 or sourcing_rules == 0) and unmet_qty > 0:
            fix_options.append({
                "action": "Add missing BOM or sourcing rules",
                "detail": f"Item currently has {bom_links} BOM link(s) and {sourcing_rules} sourcing rule(s); verify active make/buy sourcing paths in master data.",
                "impact": "High",
                "owner": "Master Data / Sourcing",
            })

        if late_qty > 0 and not any("lead time" in o["action"].lower() for o in fix_options):
            fix_options.append({
                "action": f"Pull in {_fmt_num(late_qty)} late scheduled units",
                "detail": "Evaluate pre-building ahead of peak load, expediting transit, or aligning commit dates with the business/customer.",
                "impact": "Medium",
                "owner": "Supply Planning",
            })

        if not fix_options:
            if unmet_qty > 0:
                fix_options.append({
                    "action": "Trace upstream BOM lineage in Knowledge Graph",
                    "detail": f"Drill into child components ({bom_links} BOM links) to isolate which lower-level material or resource is gating `{item_id}`.",
                    "impact": "Medium",
                    "owner": "Supply Planning",
                })
            else:
                fix_options.append({
                    "action": "No immediate corrective action required",
                    "detail": "Demand is fulfilled; monitor safety-stock coverage and bottleneck resource utilization in upcoming planning cycles.",
                    "impact": "Low",
                    "owner": "Supply Planning",
                })

        executive_summary = {
            "what_is_the_problem": {
                "headline": problem_headline,
                "bullets": problem_bullets,
            },
            "why_it_happened": {
                "headline": why_headline,
                "bullets": why_bullets,
            },
            "possible_options_to_fix": {
                "headline": f"{len(fix_options)} actionable option(s) prioritized from BY ESP evidence.",
                "options": fix_options,
            },
        }

        # Build structured 3-section markdown narrative (for API consumers, exports, and fallback rendering)
        lines: List[str] = [
            f"### {qlabel} — Item `{item_id}`",
            "\n#### 1. What is the Problem",
            problem_headline,
        ]
        for b in problem_bullets:
            lines.append(f"- {b}")

        lines.append("\n#### 2. Why the Problem Happened")
        lines.append(why_headline)
        for b in why_bullets:
            lines.append(f"- {b}")

        lines.append("\n#### 3. Possible Options to Fix the Problem")
        for idx, opt in enumerate(fix_options, start=1):
            lines.append(
                f"{idx}. **{opt['action']}** *(Impact: {opt['impact']} | Owner: {opt['owner']})* — {opt['detail']}"
            )

        lines.append(f"\n*Grounded BY ESP analysis completed in {duration_ms}ms.*")

        stats = {
            "item": item_id,
            "week": week_id or dmd.get("resolved_week") or "Latest",
            "scenario": scenario_id or dmd.get("resolved_scenario") or "Latest",
            "demand_qty": round(demand_qty, 2),
            "scheduled_qty": round(sched_qty, 2),
            "unmet_qty": round(unmet_qty, 2),
            "fill_rate_pct": fill_rate,
            "late_qty": round(late_qty, 2),
            "early_qty": round(early_qty, 2),
            "meet_status": meet_status,
            "capacity_exceptions": cap_exc_total,
            "resources_affected": len(affected_resources),
            "competing_demand_rows": competing_count,
            "on_hand_qty": round(oh_qty, 2),
            "duration_ms": duration_ms,
        }

        return {
            "question_type": qtype,
            "question_label": qlabel,
            "llm_used": False,
            "llm_model": None,
            "stats": stats,
            "executive_summary": executive_summary,
            "narrative": "\n".join(lines),
            "raw_data": {
                "Stats": stats,
                "SKU Exceptions": sku_excs,
                "Resource Exceptions": res_excs,
                "Top Supply Pegging": pegging_rows,
                "Inventory & EOH": {"on_hand_qty": round(oh_qty, 2), **eoh_row},
                "BOM & Sourcing": {"bom_links": bom_links, "sourcing_rules": sourcing_rules},
            },
        }
