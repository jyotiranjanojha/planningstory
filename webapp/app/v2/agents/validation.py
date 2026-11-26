"""DuckDB-powered Master Data & Referential Integrity Validation Agent (IFSP V2)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from ..core.data import PlanningDataEngine
from .base import AgentContext, AgentResponse


class ValidationAgent:
    """Fast DuckDB specialist for master data completeness and referential integrity."""

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def run(self, ctx: AgentContext) -> AgentResponse:
        t0 = time.perf_counter()
        checks: List[Dict[str, Any]] = []
        findings: List[str] = []
        evidence: List[str] = []
        critical_issues = 0
        warning_issues = 0

        # 1. BOM parent items missing from Item Master
        if self.data.has_table("if_snop_billofmaterials") and self.data.has_table("if_snop_items"):
            evidence.extend(["if_snop_billofmaterials", "if_snop_items"])
            row = self.data.query_one(
                """
                SELECT COUNT(DISTINCT b.ITEM) AS orphan_parents
                FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_items" i ON UPPER(TRIM(b.ITEM)) = UPPER(TRIM(i.ITEM))
                WHERE i.ITEM IS NULL AND TRIM(COALESCE(b.ITEM, '')) <> ''
                """
            ) or {}
            cnt = int(row.get("orphan_parents") or 0)
            checks.append({"check": "BOM parent -> Item master", "orphans": cnt, "severity": "Critical" if cnt else "Pass"})
            if cnt:
                critical_issues += 1
                findings.append(f"**Critical:** {cnt:,} BOM parent item(s) missing from `if_snop_items`.")

            sub_row = self.data.query_one(
                """
                SELECT COUNT(DISTINCT b.SUBORD) AS orphan_subords
                FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_items" i ON UPPER(TRIM(b.SUBORD)) = UPPER(TRIM(i.ITEM))
                WHERE i.ITEM IS NULL AND TRIM(COALESCE(b.SUBORD, '')) <> ''
                """
            ) or {}
            scnt = int(sub_row.get("orphan_subords") or 0)
            checks.append({"check": "BOM subordinate -> Item master", "orphans": scnt, "severity": "Critical" if scnt else "Pass"})
            if scnt:
                critical_issues += 1
                findings.append(f"**Critical:** {scnt:,} BOM subordinate item(s) missing from `if_snop_items`.")

        # 2. BOM locations missing from Location Master
        if self.data.has_table("if_snop_billofmaterials") and self.data.has_table("if_snop_locations"):
            evidence.append("if_snop_locations")
            row = self.data.query_one(
                """
                SELECT COUNT(DISTINCT b.LOC) AS orphan_locs
                FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_locations" l ON UPPER(TRIM(b.LOC)) = UPPER(TRIM(l.LOC))
                WHERE l.LOC IS NULL AND TRIM(COALESCE(b.LOC, '')) <> ''
                """
            ) or {}
            cnt = int(row.get("orphan_locs") or 0)
            checks.append({"check": "BOM location -> Location master", "orphans": cnt, "severity": "High" if cnt else "Pass"})
            if cnt:
                warning_issues += 1
                findings.append(f"**High:** {cnt:,} BOM location(s) missing from `if_snop_locations`.")

        # 3. SKU items missing from Item Master
        if self.data.has_table("if_snop_sku") and self.data.has_table("if_snop_items"):
            evidence.append("if_snop_sku")
            row = self.data.query_one(
                """
                SELECT COUNT(DISTINCT s.ITEM) AS orphan_sku_items
                FROM "if_snop_sku" s
                LEFT JOIN "if_snop_items" i ON UPPER(TRIM(s.ITEM)) = UPPER(TRIM(i.ITEM))
                WHERE i.ITEM IS NULL AND TRIM(COALESCE(s.ITEM, '')) <> ''
                """
            ) or {}
            cnt = int(row.get("orphan_sku_items") or 0)
            checks.append({"check": "SKU item -> Item master", "orphans": cnt, "severity": "High" if cnt else "Pass"})
            if cnt:
                warning_issues += 1
                findings.append(f"**High:** {cnt:,} SKU item(s) missing from `if_snop_items`.")

        verdict = "Fail" if critical_issues > 0 else ("Conditional Pass" if warning_issues > 0 else "Pass")
        if not findings:
            findings.append(f"All {len(checks)} referential integrity checks passed with 0 orphan keys.")

        return AgentResponse(
            agent="ValidationAgent",
            workflow="Validation Gate (V2)",
            summary=f"**Validation Readiness Verdict: {verdict}** ({len(checks)} checks executed; {critical_issues} critical, {warning_issues} warning).",
            confirmed_findings=findings,
            metrics={
                "verdict": verdict,
                "checks_executed": len(checks),
                "critical_issues": critical_issues,
                "warning_issues": warning_issues,
            },
            rows=checks,
            evidence_sources=sorted(set(evidence)),
            confidence="High",
            recommended_next_checks=["Inspect orphan keys in `by_input/` before running the next BY ESP solve."] if verdict != "Pass" else ["Proceed to fulfillment and scenario analysis."],
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    def run_planning_parameters_gate(self, week_id: str | None = None, scenario_id: str | None = None) -> Dict[str, Any]:
        """DuckDB-powered Planning Parameters Validation Gate (lead times, sourcing, calendars, inventory, orders)."""
        t0 = time.perf_counter()
        checks: Dict[str, Any] = {}
        critical: List[str] = []
        high: List[str] = []
        medium: List[str] = []
        low: List[str] = []
        details: List[Dict[str, Any]] = []
        evidence: List[str] = []

        # 1. Production method lead time sanity
        if self.data.has_table("if_snop_productionmethod"):
            evidence.append("if_snop_productionmethod")
            row = self.data.query_one(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN TRY_CAST(LEADTIME AS DOUBLE) < 0 THEN 1 ELSE 0 END) AS neg_lt,
                    SUM(CASE WHEN TRIM(COALESCE(LEADTIME, '')) = '' THEN 1 ELSE 0 END) AS blank_lt
                FROM "if_snop_productionmethod"
                """
            ) or {}
            neg_lt = int(row.get("neg_lt") or 0)
            blank_lt = int(row.get("blank_lt") or 0)
            checks["prod_method_negative_leadtime"] = neg_lt
            checks["prod_method_blank_leadtime"] = blank_lt
            if neg_lt > 0:
                critical.append(f"Production methods with negative lead time: {neg_lt}")
            if blank_lt > 0:
                medium.append(f"Production methods with blank lead time: {blank_lt}")
            details.append({"Check": "Production Method Lead Time", "Total Rows": int(row.get("total") or 0), "Negative": neg_lt, "Blank": blank_lt, "Status": "FAIL" if neg_lt else ("WARN" if blank_lt else "PASS")})

        # 2. Purchase method lead time sanity
        if self.data.has_table("if_snop_purchmethod"):
            evidence.append("if_snop_purchmethod")
            row = self.data.query_one(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN TRY_CAST(LEADTIME AS DOUBLE) < 0 THEN 1 ELSE 0 END) AS neg_lt,
                    SUM(CASE WHEN TRIM(COALESCE(LEADTIME, '')) = '' THEN 1 ELSE 0 END) AS blank_lt
                FROM "if_snop_purchmethod"
                """
            ) or {}
            neg_lt = int(row.get("neg_lt") or 0)
            blank_lt = int(row.get("blank_lt") or 0)
            checks["purch_method_negative_leadtime"] = neg_lt
            checks["purch_method_blank_leadtime"] = blank_lt
            if neg_lt > 0:
                critical.append(f"Purchase methods with negative lead time: {neg_lt}")
            if blank_lt > 0:
                medium.append(f"Purchase methods with blank lead time: {blank_lt}")
            details.append({"Check": "Purchase Method Lead Time", "Total Rows": int(row.get("total") or 0), "Negative": neg_lt, "Blank": blank_lt, "Status": "FAIL" if neg_lt else ("WARN" if blank_lt else "PASS")})

        # 3. Sourcing factor & location validity
        if self.data.has_table("if_snop_sourcing"):
            evidence.append("if_snop_sourcing")
            row = self.data.query_one(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN TRY_CAST(FACTOR AS DOUBLE) <= 0 THEN 1 ELSE 0 END) AS invalid_factor
                FROM "if_snop_sourcing"
                """
            ) or {}
            inv_f = int(row.get("invalid_factor") or 0)
            checks["sourcing_invalid_factor"] = inv_f
            if inv_f > 0:
                high.append(f"Sourcing rules with zero/negative split factor: {inv_f}")
            details.append({"Check": "Sourcing Split Factor (>0)", "Total Rows": int(row.get("total") or 0), "Invalid": inv_f, "Status": "FAIL" if inv_f else "PASS"})

        # 4. Resource calendar reference integrity
        if self.data.has_table("if_snop_res") and self.data.has_table("if_snop_calendars"):
            evidence.extend(["if_snop_res", "if_snop_calendars"])
            row = self.data.query_one(
                """
                SELECT COUNT(*) AS orphan_cals
                FROM "if_snop_res" r
                LEFT JOIN "if_snop_calendars" c ON UPPER(TRIM(r.CAL)) = UPPER(TRIM(c.CAL))
                WHERE TRIM(COALESCE(r.CAL, '')) <> '' AND c.CAL IS NULL
                """
            ) or {}
            oc = int(row.get("orphan_cals") or 0)
            checks["resource_missing_calendar"] = oc
            if oc > 0:
                high.append(f"Resources referencing missing calendars: {oc}")
            details.append({"Check": "Resource Calendar Integrity", "Orphan Calendars": oc, "Status": "FAIL" if oc else "PASS"})

        # 5. Negative on-hand inventory
        if self.data.has_table("if_snop_inventory"):
            evidence.append("if_snop_inventory")
            row = self.data.query_one(
                """
                SELECT COUNT(*) AS total, SUM(CASE WHEN TRY_CAST(QTY AS DOUBLE) < 0 THEN 1 ELSE 0 END) AS neg_qty
                FROM "if_snop_inventory"
                """
            ) or {}
            nq = int(row.get("neg_qty") or 0)
            checks["inventory_negative_qty"] = nq
            if nq > 0:
                high.append(f"Negative on-hand inventory records: {nq}")
            details.append({"Check": "On-Hand Inventory Non-Negative", "Total Rows": int(row.get("total") or 0), "Negative": nq, "Status": "FAIL" if nq else "PASS"})

        # 6. Zero/negative customer orders
        if self.data.has_table("if_snop_customerorder"):
            evidence.append("if_snop_customerorder")
            row = self.data.query_one(
                """
                SELECT COUNT(*) AS total, SUM(CASE WHEN TRY_CAST(QTY AS DOUBLE) <= 0 THEN 1 ELSE 0 END) AS bad_qty
                FROM "if_snop_customerorder"
                """
            ) or {}
            bq = int(row.get("bad_qty") or 0)
            checks["customer_order_nonpositive_qty"] = bq
            if bq > 0:
                medium.append(f"Customer orders with zero or negative QTY: {bq}")
            details.append({"Check": "Customer Order Positive QTY", "Total Rows": int(row.get("total") or 0), "Non-Positive": bq, "Status": "WARN" if bq else "PASS"})

        verdict = "Fail" if critical else ("Conditional Pass" if (high or medium) else "Pass")
        return {
            "Validation Scope": {"week_id": week_id, "scenario_id": scenario_id, "focus_areas": ["planning_parameters"], "duration_ms": int((time.perf_counter() - t0) * 1000)},
            "Readiness Verdict (Pass, Conditional Pass, Fail)": verdict,
            "Checks Executed": checks,
            "Issues Found (Critical, High, Medium, Low)": {"Critical": critical, "High": high, "Medium": medium, "Low": low},
            "Parameter Check Details": details,
            "Datasets and Evidence Used": {"tables": sorted(set(evidence))},
            "Recommended Fixes (ordered by impact)": [
                "Repair any negative lead times or invalid sourcing factors before running BY ESP solve.",
                "Align resource CAL IDs with `if_snop_calendars` so capacity buckets evaluate properly.",
            ],
        }

    def run_output_sanity_gate(self, week_id: str | None = None, scenario_id: str | None = None) -> Dict[str, Any]:
        """DuckDB-powered Post-Solve Output Sanity & Solver Health Validation Gate."""
        t0 = time.perf_counter()
        checks: Dict[str, Any] = {}
        critical: List[str] = []
        high: List[str] = []
        medium: List[str] = []
        low: List[str] = []
        details: List[Dict[str, Any]] = []
        evidence: List[str] = []

        # 1. Demand coverage & fill rate sanity
        if self.data.has_table("by_if_snop_out_inddmdview"):
            evidence.append("by_if_snop_out_inddmdview")
            row = self.data.query_one(
                """
                SELECT
                    COUNT(*) AS lines,
                    COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0) AS dmd,
                    COALESCE(SUM(TRY_CAST(SCHEDQTY AS DOUBLE)), 0) AS sch,
                    SUM(CASE WHEN TRY_CAST(SCHEDQTY AS DOUBLE) < 0 THEN 1 ELSE 0 END) AS neg_sched
                FROM "by_if_snop_out_inddmdview"
                """
            ) or {}
            dmd = float(row.get("dmd") or 0)
            sch = float(row.get("sch") or 0)
            fr = round((sch / dmd * 100.0) if dmd > 0 else 0.0, 1)
            neg_s = int(row.get("neg_sched") or 0)
            checks["demand_lines"] = int(row.get("lines") or 0)
            checks["fill_rate_pct"] = fr
            checks["negative_scheduled_qty"] = neg_s
            if neg_s > 0:
                critical.append(f"Negative scheduled quantities in demand view: {neg_s}")
            if dmd > 0 and fr < 80.0:
                high.append(f"Low overall demand fill rate: {fr}% (below 80% threshold)")
            details.append({"Check": "Demand Fill Rate & Coverage", "Demand Lines": int(row.get("lines") or 0), "Fill Rate %": fr, "Negative Sched": neg_s, "Status": "FAIL" if (neg_s or fr < 50) else ("WARN" if fr < 80 else "PASS")})

        # 2. Planned orders completeness & non-negative check
        if self.data.has_table("by_if_snop_out_planorder"):
            evidence.append("by_if_snop_out_planorder")
            row = self.data.query_one(
                """
                SELECT COUNT(*) AS rows, SUM(CASE WHEN TRY_CAST(QTY AS DOUBLE) <= 0 THEN 1 ELSE 0 END) AS nonpos
                FROM "by_if_snop_out_planorder"
                """
            ) or {}
            cnt = int(row.get("rows") or 0)
            nonpos = int(row.get("nonpos") or 0)
            checks["planorder_rows"] = cnt
            checks["planorder_nonpositive_qty"] = nonpos
            if cnt == 0:
                high.append("Zero planned orders generated in `by_if_snop_out_planorder`")
            if nonpos > 0:
                medium.append(f"Planned orders with zero/negative QTY: {nonpos}")
            details.append({"Check": "Planned Orders Completeness", "Rows": cnt, "Non-Positive QTY": nonpos, "Status": "FAIL" if cnt == 0 else ("WARN" if nonpos else "PASS")})

        # 3. SKU exceptions volume
        if self.data.has_table("by_if_snop_out_skuexception"):
            evidence.append("by_if_snop_out_skuexception")
            row = self.data.query_one('SELECT COUNT(*) AS c, COUNT(DISTINCT ITEM) AS items FROM "by_if_snop_out_skuexception"') or {}
            cnt = int(row.get("c") or 0)
            checks["sku_exceptions"] = cnt
            if cnt > 0:
                high.append(f"Active SKU exceptions: {cnt:,} across {int(row.get('items') or 0)} item(s)")
            details.append({"Check": "SKU Exceptions", "Exception Count": cnt, "Items Affected": int(row.get("items") or 0), "Status": "WARN" if cnt > 0 else "PASS"})

        # 4. Resource capacity exceptions volume
        if self.data.has_table("by_if_snop_out_resexception"):
            evidence.append("by_if_snop_out_resexception")
            row = self.data.query_one('SELECT COUNT(*) AS c, COUNT(DISTINCT RES) AS res FROM "by_if_snop_out_resexception"') or {}
            cnt = int(row.get("c") or 0)
            checks["resource_exceptions"] = cnt
            if cnt > 0:
                medium.append(f"Active Resource capacity exceptions: {cnt:,} across {int(row.get('res') or 0)} resource(s)")
            details.append({"Check": "Resource Capacity Exceptions", "Exception Count": cnt, "Resources Affected": int(row.get("res") or 0), "Status": "WARN" if cnt > 0 else "PASS"})

        verdict = "Fail" if critical else ("Conditional Pass" if (high or medium) else "Pass")
        return {
            "Validation Scope": {"week_id": week_id, "scenario_id": scenario_id, "focus_areas": ["output_sanity"], "duration_ms": int((time.perf_counter() - t0) * 1000)},
            "Readiness Verdict (Pass, Conditional Pass, Fail)": verdict,
            "Checks Executed": checks,
            "Issues Found (Critical, High, Medium, Low)": {"Critical": critical, "High": high, "Medium": medium, "Low": low},
            "Output Sanity Details": details,
            "Datasets and Evidence Used": {"tables": sorted(set(evidence))},
            "Recommended Fixes (ordered by impact)": [
                "Inspect top SKU exceptions (Material, Lead Time, Invalid BOM) driving unmet demand.",
                "Review overloaded resource exceptions in Capacity & Knowledge Graph views.",
            ],
        }
