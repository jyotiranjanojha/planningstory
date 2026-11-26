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

    def run_bom_traversal_gate(
        self,
        week_id: str | None = None,
        scenario_id: str | None = None,
        item_id: str | None = None,
    ) -> Dict[str, Any]:
        """Issue-focused DuckDB BOM Traversal & Structural Integrity Gate (no giant graph)."""
        t0 = time.perf_counter()
        checks: Dict[str, Any] = {}
        critical: List[str] = []
        high: List[str] = []
        medium: List[str] = []
        low: List[str] = []
        issue_rows: List[Dict[str, Any]] = []
        evidence: List[str] = []

        if not self.data.has_table("if_snop_billofmaterials"):
            return {
                "Validation Scope": {"week_id": week_id, "scenario_id": scenario_id, "item_id": item_id, "focus_areas": ["bom_traversal"]},
                "Readiness Verdict (Pass, Conditional Pass, Fail)": "Fail",
                "Checks Executed": {},
                "Issues Found (Critical, High, Medium, Low)": {"Critical": ["Missing required table: if_snop_billofmaterials"], "High": [], "Medium": [], "Low": []},
                "BOM Issue Details": [],
                "Datasets and Evidence Used": {"tables": []},
                "Recommended Fixes (ordered by impact)": ["Load `if_snop_billofmaterials` into `by_input/` and re-run validation."],
            }

        evidence.append("if_snop_billofmaterials")
        item_filter = (item_id or "").strip()
        base_where = "WHERE UPPER(TRIM(ITEM)) = UPPER(TRIM(?))" if item_filter else ""
        base_params: List[Any] = [item_filter] if item_filter else []

        # 1. Coverage & topology metrics
        agg = self.data.query_one(
            f"""
            SELECT
                COUNT(*) AS bom_rows_total,
                COUNT(DISTINCT TRIM(ITEM)) AS parent_items_total,
                COUNT(DISTINCT TRIM(SUBORD)) AS component_items_total,
                COUNT(DISTINCT TRIM(LOC)) AS bom_locations_total
            FROM "if_snop_billofmaterials"
            {base_where}
            """,
            base_params,
        ) or {}
        checks["bom_rows_total"] = int(agg.get("bom_rows_total") or 0)
        checks["parent_items_total"] = int(agg.get("parent_items_total") or 0)
        checks["component_items_total"] = int(agg.get("component_items_total") or 0)

        # 2. Cycle detection & max traversal depth (in-memory DFS on distinct ITEM -> SUBORD edges)
        edge_rows = self.data.query(
            'SELECT DISTINCT TRIM(ITEM) AS p, TRIM(SUBORD) AS c FROM "if_snop_billofmaterials" WHERE TRIM(COALESCE(ITEM, \'\')) <> \'\' AND TRIM(COALESCE(SUBORD, \'\')) <> \'\''
        )
        adj: Dict[str, List[str]] = {}
        for er in edge_rows:
            adj.setdefault(er["p"], []).append(er["c"])

        roots = [item_filter] if item_filter else list(adj.keys())
        cycle_paths: List[str] = []
        max_depth = 0
        memo_depth: Dict[str, int] = {}

        def _dfs(node: str, path: List[str], path_set: set[str]) -> int:
            nonlocal max_depth
            if node in memo_depth:
                return memo_depth[node]
            best = 0
            for child in adj.get(node, ()):
                if child in path_set:
                    if len(cycle_paths) < 10:
                        idx = path.index(child)
                        cycle_paths.append(" -> ".join(path[idx:] + [child]))
                    continue
                if len(path) >= 20:
                    continue
                path.append(child)
                path_set.add(child)
                d = 1 + _dfs(child, path, path_set)
                path.pop()
                path_set.remove(child)
                if d > best:
                    best = d
            memo_depth[node] = best
            if best > max_depth:
                max_depth = best
            return best

        for rt in roots:
            if rt in adj:
                _dfs(rt, [rt], {rt})

        checks["bom_max_depth"] = max_depth
        checks["bom_circular_cycles"] = len(cycle_paths)
        if cycle_paths:
            critical.append(f"Circular BOM loop(s) detected: {len(cycle_paths)} (e.g. {cycle_paths[0]})")
            for cp in cycle_paths[:5]:
                issue_rows.append({"Severity": "Critical", "Issue Type": "Circular BOM Cycle", "Parent Item": cp.split(" -> ")[0], "Component": cp.split(" -> ")[1] if " -> " in cp else "", "Location": "All", "BOM #": "-", "Detail": cp})

        # 3. Self-referencing BOM rows (ITEM == SUBORD)
        self_rows = self.data.query(
            f"""
            SELECT TRIM(ITEM) AS item, TRIM(SUBORD) AS subord, TRIM(LOC) AS loc, TRIM(BOMNUM) AS bomnum
            FROM "if_snop_billofmaterials"
            {"WHERE UPPER(TRIM(ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} UPPER(TRIM(ITEM)) = UPPER(TRIM(SUBORD))
            LIMIT 10
            """,
            base_params,
        )
        checks["bom_self_referencing_rows"] = len(self_rows)
        if self_rows:
            critical.append(f"Self-referencing BOM rows (ITEM = SUBORD): {len(self_rows)}")
            for r in self_rows[:5]:
                issue_rows.append({"Severity": "Critical", "Issue Type": "Self-Referencing BOM", "Parent Item": r["item"], "Component": r["subord"], "Location": r["loc"], "BOM #": r["bomnum"], "Detail": "Parent item references itself as a subordinate"})

        # 4. Invalid / non-positive DRAWQTY
        bad_draw = self.data.query(
            f"""
            SELECT TRIM(ITEM) AS item, TRIM(SUBORD) AS subord, TRIM(LOC) AS loc, TRIM(BOMNUM) AS bomnum, DRAWQTY
            FROM "if_snop_billofmaterials"
            {"WHERE UPPER(TRIM(ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} (TRY_CAST(DRAWQTY AS DOUBLE) IS NULL OR TRY_CAST(DRAWQTY AS DOUBLE) <= 0)
            LIMIT 10
            """,
            base_params,
        )
        checks["bom_invalid_draw_qty"] = len(bad_draw)
        if bad_draw:
            critical.append(f"BOM rows with blank, zero, or negative DRAWQTY: {len(bad_draw)}")
            for r in bad_draw[:5]:
                issue_rows.append({"Severity": "Critical", "Issue Type": "Invalid Draw Qty", "Parent Item": r["item"], "Component": r["subord"], "Location": r["loc"], "BOM #": r["bomnum"], "Detail": f"DRAWQTY={r['DRAWQTY']}"})

        # 5. Duplicate BOM lines (ITEM, SUBORD, LOC, BOMNUM)
        dup_rows = self.data.query(
            f"""
            SELECT TRIM(ITEM) AS item, TRIM(SUBORD) AS subord, TRIM(LOC) AS loc, TRIM(BOMNUM) AS bomnum, COUNT(*) AS cnt
            FROM "if_snop_billofmaterials"
            {base_where}
            GROUP BY 1, 2, 3, 4
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC
            LIMIT 10
            """,
            base_params,
        )
        checks["bom_duplicate_lines"] = len(dup_rows)
        if dup_rows:
            medium.append(f"Duplicate BOM lines (same ITEM + SUBORD + LOC + BOMNUM): {len(dup_rows)}")
            for r in dup_rows[:5]:
                issue_rows.append({"Severity": "Medium", "Issue Type": "Duplicate BOM Line", "Parent Item": r["item"], "Component": r["subord"], "Location": r["loc"], "BOM #": r["bomnum"], "Detail": f"{r['cnt']} duplicate rows"})

        # 6. Orphan parent / component items missing from if_snop_items
        if self.data.has_table("if_snop_items"):
            evidence.append("if_snop_items")
            op_cnt = int((self.data.query_one(
                f"""
                SELECT COUNT(*) AS c FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_items" i ON UPPER(TRIM(b.ITEM)) = UPPER(TRIM(i.ITEM))
                {"WHERE UPPER(TRIM(b.ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} i.ITEM IS NULL
                """,
                base_params,
            ) or {}).get("c") or 0)
            oc_cnt = int((self.data.query_one(
                f"""
                SELECT COUNT(*) AS c FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_items" i ON UPPER(TRIM(b.SUBORD)) = UPPER(TRIM(i.ITEM))
                {"WHERE UPPER(TRIM(b.ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} i.ITEM IS NULL
                """,
                base_params,
            ) or {}).get("c") or 0)
            checks["bom_orphan_parent_item"] = op_cnt
            checks["bom_orphan_component_item"] = oc_cnt
            if op_cnt > 0:
                high.append(f"BOM rows with parent ITEM missing from Item Master: {op_cnt}")
            if oc_cnt > 0:
                high.append(f"BOM rows with component SUBORD missing from Item Master: {oc_cnt}")

        # 7. Component missing SKU at BOM location
        if self.data.has_table("if_snop_sku"):
            evidence.append("if_snop_sku")
            miss_sku = self.data.query(
                f"""
                SELECT DISTINCT TRIM(b.ITEM) AS item, TRIM(b.SUBORD) AS subord, TRIM(b.LOC) AS loc, TRIM(b.BOMNUM) AS bomnum
                FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_sku" s ON UPPER(TRIM(b.SUBORD)) = UPPER(TRIM(s.ITEM)) AND UPPER(TRIM(b.LOC)) = UPPER(TRIM(s.LOC))
                {"WHERE UPPER(TRIM(b.ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} s.ITEM IS NULL
                LIMIT 12
                """,
                base_params,
            )
            miss_sku_cnt = int((self.data.query_one(
                f"""
                SELECT COUNT(*) AS c FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_sku" s ON UPPER(TRIM(b.SUBORD)) = UPPER(TRIM(s.ITEM)) AND UPPER(TRIM(b.LOC)) = UPPER(TRIM(s.LOC))
                {"WHERE UPPER(TRIM(b.ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} s.ITEM IS NULL
                """,
                base_params,
            ) or {}).get("c") or 0)
            checks["bom_component_missing_sku"] = miss_sku_cnt
            if miss_sku_cnt > 0:
                high.append(f"BOM component(s) missing SKU master record at BOM location: {miss_sku_cnt}")
                for r in miss_sku[:6]:
                    issue_rows.append({"Severity": "High", "Issue Type": "Component Missing SKU", "Parent Item": r["item"], "Component": r["subord"], "Location": r["loc"], "BOM #": r["bomnum"], "Detail": f"No SKU record for {r['subord']} @ {r['loc']}"})

        # 8. BOM triplets missing a Production Method
        if self.data.has_table("if_snop_productionmethod"):
            evidence.append("if_snop_productionmethod")
            no_pm = self.data.query(
                f"""
                SELECT DISTINCT TRIM(b.ITEM) AS item, TRIM(b.SUBORD) AS subord, TRIM(b.LOC) AS loc, TRIM(b.BOMNUM) AS bomnum
                FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_productionmethod" p
                  ON UPPER(TRIM(b.ITEM)) = UPPER(TRIM(p.ITEM))
                 AND UPPER(TRIM(b.LOC)) = UPPER(TRIM(p.LOC))
                 AND UPPER(TRIM(COALESCE(b.BOMNUM, ''))) = UPPER(TRIM(COALESCE(p.BOMNUM, '')))
                {"WHERE UPPER(TRIM(b.ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} p.ITEM IS NULL
                LIMIT 12
                """,
                base_params,
            )
            no_pm_cnt = int((self.data.query_one(
                f"""
                SELECT COUNT(DISTINCT TRIM(b.ITEM) || '|' || TRIM(b.LOC) || '|' || TRIM(COALESCE(b.BOMNUM, ''))) AS c
                FROM "if_snop_billofmaterials" b
                LEFT JOIN "if_snop_productionmethod" p
                  ON UPPER(TRIM(b.ITEM)) = UPPER(TRIM(p.ITEM))
                 AND UPPER(TRIM(b.LOC)) = UPPER(TRIM(p.LOC))
                 AND UPPER(TRIM(COALESCE(b.BOMNUM, ''))) = UPPER(TRIM(COALESCE(p.BOMNUM, '')))
                {"WHERE UPPER(TRIM(b.ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} p.ITEM IS NULL
                """,
                base_params,
            ) or {}).get("c") or 0)
            checks["bom_missing_production_method"] = no_pm_cnt
            if no_pm_cnt > 0:
                high.append(f"BOM(s) with no matching Production Method: {no_pm_cnt}")
                for r in no_pm[:6]:
                    issue_rows.append({"Severity": "High", "Issue Type": "Missing Production Method", "Parent Item": r["item"], "Component": r["subord"], "Location": r["loc"], "BOM #": r["bomnum"], "Detail": f"No production method for ({r['item']}, {r['loc']}, BOM {r['bomnum']})"})

        # 9. Dead-end / unsourceable leaf components (not a BOM parent, not purchased, not sourced, no on-hand)
        leaf_sources: List[str] = ['SELECT DISTINCT UPPER(TRIM(ITEM)) AS k FROM "if_snop_billofmaterials"']
        if self.data.has_table("if_snop_purchmethod"):
            evidence.append("if_snop_purchmethod")
            leaf_sources.append('SELECT DISTINCT UPPER(TRIM(ITEM)) AS k FROM "if_snop_purchmethod"')
        if self.data.has_table("if_snop_sourcing"):
            evidence.append("if_snop_sourcing")
            leaf_sources.append('SELECT DISTINCT UPPER(TRIM(ITEM)) AS k FROM "if_snop_sourcing"')
        if self.data.has_table("if_snop_inventory"):
            evidence.append("if_snop_inventory")
            leaf_sources.append('SELECT DISTINCT UPPER(TRIM(ITEM)) AS k FROM "if_snop_inventory" WHERE TRY_CAST(QTY AS DOUBLE) > 0')
        union_sql = " UNION ".join(leaf_sources)
        dead_leaves = self.data.query(
            f"""
            WITH supply_known AS ({union_sql})
            SELECT DISTINCT TRIM(b.ITEM) AS item, TRIM(b.SUBORD) AS subord, TRIM(b.LOC) AS loc, TRIM(b.BOMNUM) AS bomnum
            FROM "if_snop_billofmaterials" b
            LEFT JOIN supply_known s ON UPPER(TRIM(b.SUBORD)) = s.k
            {"WHERE UPPER(TRIM(b.ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} s.k IS NULL
            LIMIT 12
            """,
            base_params,
        )
        dead_leaf_cnt = int((self.data.query_one(
            f"""
            WITH supply_known AS ({union_sql})
            SELECT COUNT(DISTINCT UPPER(TRIM(b.SUBORD))) AS c
            FROM "if_snop_billofmaterials" b
            LEFT JOIN supply_known s ON UPPER(TRIM(b.SUBORD)) = s.k
            {"WHERE UPPER(TRIM(b.ITEM)) = UPPER(TRIM(?)) AND" if item_filter else "WHERE"} s.k IS NULL
            """,
            base_params,
        ) or {}).get("c") or 0)
        checks["bom_unsourceable_leaf_components"] = dead_leaf_cnt
        if dead_leaf_cnt > 0:
            medium.append(f"Unsourceable leaf BOM components (no BOM, Purchase, Sourcing, or On-Hand): {dead_leaf_cnt}")
            for r in dead_leaves[:6]:
                issue_rows.append({"Severity": "Medium", "Issue Type": "Unsourceable Leaf Component", "Parent Item": r["item"], "Component": r["subord"], "Location": r["loc"], "BOM #": r["bomnum"], "Detail": "Leaf component has no purchase method, sourcing rule, or on-hand inventory"})

        verdict = "Fail" if critical else ("Conditional Pass" if (high or medium) else "Pass")
        return {
            "Validation Scope": {
                "week_id": week_id or "Latest",
                "scenario_id": scenario_id or "Latest",
                "item_id": item_filter or "All BOMs",
                "focus_areas": ["bom_traversal"],
                "duration_ms": int((time.perf_counter() - t0) * 1000),
            },
            "Readiness Verdict (Pass, Conditional Pass, Fail)": verdict,
            "Checks Executed": checks,
            "Issues Found (Critical, High, Medium, Low)": {"Critical": critical, "High": high, "Medium": medium, "Low": low},
            "BOM Issue Details": issue_rows,
            "Datasets and Evidence Used": {"tables": sorted(set(evidence))},
            "Root Causes and Likely Planning Impact": [
                "Circular BOM loops or invalid DRAWQTY values prevent BY ESP from exploding dependent demand cleanly.",
                "Components missing SKU records, production methods, or leaf supply paths cause solver material shortages and unmet demand.",
            ],
            "Recommended Fixes (ordered by impact)": [
                "Resolve all Critical BOM defects first (circular cycles, self-references, zero/negative DRAWQTY).",
                "Create missing SKU master records and production methods for flagged BOM triplets.",
                "Add purchase methods, sourcing lanes, or initial inventory for unsourceable leaf components.",
            ],
            "Confidence and Data Gaps": {"confidence": "High", "data_gaps": []},
        }
