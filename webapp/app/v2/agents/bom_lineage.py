"""DuckDB-powered BOM Explosion, Alternate BOM & Supersession Lineage Agent (IFSP V2)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from ..core.data import PlanningDataEngine
from .base import AgentContext, AgentResponse
from .fulfillment import _fmt_num


class BomLineageAgent:
    """Fast DuckDB specialist for multi-level BOM explosion, where-used, alt BOM, and supersession."""

    BOM_TABLE = "if_snop_billofmaterials"
    ALTBOM_TABLE = "if_snop_altbillofmaterials"
    SUPER_TABLE = "if_snop_supersession"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def run(self, ctx: AgentContext) -> AgentResponse:
        t0 = time.perf_counter()
        if not self.data.has_table(self.BOM_TABLE):
            return AgentResponse(
                agent="BomLineageAgent",
                workflow="BOM & Genealogy Lineage (V2)",
                summary="BOM input table (`if_snop_billofmaterials`) is not available in `by_input/`.",
                confidence="Low",
                data_gaps=[f"Missing {self.BOM_TABLE}"],
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        evidence = [self.BOM_TABLE]
        findings: List[str] = []
        root_causes: List[str] = []
        rows: List[Dict[str, Any]] = []

        target_items = [ctx.item_id] if ctx.item_id else list(ctx.item_ids or [])

        if target_items:
            # Multi-level BOM explosion (children) + Where-Used (parents)
            ph = ", ".join(["UPPER(TRIM(?))"] * len(target_items))
            children = self.data.query(
                f"""
                WITH RECURSIVE bom_tree AS (
                    SELECT
                        1 AS level,
                        TRIM(ITEM) AS parent_item,
                        TRIM(SUBORD) AS child_item,
                        TRIM(LOC) AS loc,
                        TRIM(BOMNUM) AS bomnum,
                        TRY_CAST(DRAWQTY AS DOUBLE) AS draw_qty
                    FROM "{self.BOM_TABLE}"
                    WHERE UPPER(TRIM(ITEM)) IN ({ph})
                    UNION ALL
                    SELECT
                        t.level + 1,
                        TRIM(b.ITEM),
                        TRIM(b.SUBORD),
                        TRIM(b.LOC),
                        TRIM(b.BOMNUM),
                        TRY_CAST(b.DRAWQTY AS DOUBLE)
                    FROM bom_tree t
                    JOIN "{self.BOM_TABLE}" b
                      ON UPPER(TRIM(b.ITEM)) = UPPER(t.child_item)
                    WHERE t.level < 4
                )
                SELECT DISTINCT level, parent_item, child_item, loc, bomnum, ROUND(COALESCE(draw_qty, 0), 4) AS draw_qty
                FROM bom_tree
                ORDER BY level, parent_item, child_item
                LIMIT 25
                """,
                target_items,
            )

            parents = self.data.query(
                f"""
                SELECT DISTINCT TRIM(ITEM) AS parent_item, TRIM(SUBORD) AS child_item, TRIM(LOC) AS loc, TRIM(BOMNUM) AS bomnum
                FROM "{self.BOM_TABLE}"
                WHERE UPPER(TRIM(SUBORD)) IN ({ph})
                ORDER BY 1, 2
                LIMIT 15
                """,
                target_items,
            )

            alt_rows: List[Dict[str, Any]] = []
            if self.data.has_table(self.ALTBOM_TABLE):
                evidence.append(self.ALTBOM_TABLE)
                alt_rows = self.data.query(
                    f"""
                    SELECT DISTINCT TRIM(ITEM) AS item, TRIM(SUBORD) AS primary_subord, TRIM(ALTSUBORD) AS alt_subord, TRIM(LOC) AS loc
                    FROM "{self.ALTBOM_TABLE}"
                    WHERE UPPER(TRIM(ITEM)) IN ({ph}) OR UPPER(TRIM(SUBORD)) IN ({ph})
                    LIMIT 10
                    """,
                    target_items + target_items,
                )

            sup_rows: List[Dict[str, Any]] = []
            if self.data.has_table(self.SUPER_TABLE):
                evidence.append(self.SUPER_TABLE)
                sup_rows = self.data.query(
                    f"""
                    SELECT DISTINCT TRIM(ITEM) AS item, TRIM(ALTITEM) AS alt_item, TRIM(LOC) AS loc
                    FROM "{self.SUPER_TABLE}"
                    WHERE UPPER(TRIM(ITEM)) IN ({ph}) OR UPPER(TRIM(ALTITEM)) IN ({ph})
                    LIMIT 10
                    """,
                    target_items + target_items,
                )

            scope_title = f"Item `{ctx.item_id}`" if ctx.item_id else (ctx.item_filter_label or f"{len(target_items)} items")
            max_depth = max((int(r["level"]) for r in children), default=0)

            summary = (
                f"**BOM & Lineage — {scope_title}:** Found **{_fmt_num(len(children))}** downstream BOM component link(s) "
                f"(max depth **{max_depth}**), **{_fmt_num(len(parents))}** upstream parent link(s), "
                f"**{_fmt_num(len(alt_rows))}** alternate BOM rule(s), and **{_fmt_num(len(sup_rows))}** supersession rule(s)."
            )

            if children:
                sample = "; ".join(f"L{r['level']}: `{r['parent_item']}` -> `{r['child_item']}` (draw {r['draw_qty']})" for r in children[:6])
                findings.append(f"Downstream components: {sample}.")
                rows = children
            if parents:
                p_str = "; ".join(f"`{r['parent_item']}` uses `{r['child_item']}` @ `{r['loc']}`" for r in parents[:6])
                findings.append(f"Where-used (upstream parents): {p_str}.")
                if not rows:
                    rows = parents
            if alt_rows:
                a_str = "; ".join(f"`{r['item']}`: `{r['primary_subord']}` -> alt `{r['alt_subord']}`" for r in alt_rows[:5])
                root_causes.append(f"Alternate BOM options: {a_str}.")
            if sup_rows:
                s_str = "; ".join(f"`{r['item']}` <-> `{r['alt_item']}` @ `{r['loc']}`" for r in sup_rows[:5])
                root_causes.append(f"Supersession links: {s_str}.")

            next_checks = []
            if parents:
                next_checks.append(f"Why is demand unmet for item {parents[0]['parent_item']}?")
            if children:
                next_checks.append(f"How does the inventory position look for item {children[0]['child_item']}?")
            next_checks.append("Run master data validation check")

            return AgentResponse(
                agent="BomLineageAgent",
                workflow="BOM & Genealogy Lineage (V2)",
                summary=summary,
                confirmed_findings=findings,
                root_causes=root_causes,
                metrics={
                    "downstream_links": len(children),
                    "upstream_parents": len(parents),
                    "max_depth": max_depth,
                    "alt_bom_rules": len(alt_rows),
                    "supersession_rules": len(sup_rows),
                },
                rows=rows,
                evidence_sources=evidence,
                confidence="High",
                recommended_next_checks=next_checks,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        # Portfolio-wide BOM summary
        bom_agg = self.data.query_one(
            f"""
            SELECT
                COUNT(*) AS total_links,
                COUNT(DISTINCT ITEM) AS parent_items,
                COUNT(DISTINCT SUBORD) AS child_items,
                COUNT(DISTINCT LOC) AS locs
            FROM "{self.BOM_TABLE}"
            """
        ) or {}

        alt_cnt = 0
        if self.data.has_table(self.ALTBOM_TABLE):
            evidence.append(self.ALTBOM_TABLE)
            alt_cnt = int((self.data.query_one(f'SELECT COUNT(*) AS c FROM "{self.ALTBOM_TABLE}"') or {}).get("c") or 0)

        sup_cnt = 0
        if self.data.has_table(self.SUPER_TABLE):
            evidence.append(self.SUPER_TABLE)
            sup_cnt = int((self.data.query_one(f'SELECT COUNT(*) AS c FROM "{self.SUPER_TABLE}"') or {}).get("c") or 0)

        top_parents = self.data.query(
            f"""
            SELECT TRIM(ITEM) AS parent_item, COUNT(DISTINCT SUBORD) AS component_count, COUNT(DISTINCT LOC) AS locations
            FROM "{self.BOM_TABLE}"
            GROUP BY 1
            ORDER BY component_count DESC
            LIMIT 6
            """
        )
        if top_parents:
            top_str = "; ".join(f"`{r['parent_item']}` ({r['component_count']} components)" for r in top_parents[:5])
            findings.append(f"Widest BOM assemblies: {top_str}.")
        findings.append(
            f"Alternate BOM rules: **{_fmt_num(alt_cnt)}** | Supersession rules: **{_fmt_num(sup_cnt)}**."
        )

        summary = (
            f"**BOM Portfolio Summary:** **{_fmt_num(bom_agg.get('total_links'))}** BOM links connecting "
            f"**{_fmt_num(bom_agg.get('parent_items'))}** parent items to **{_fmt_num(bom_agg.get('child_items'))}** subordinate items across **{_fmt_num(bom_agg.get('locs'))}** locations."
        )

        next_checks = [
            f"Show BOM lineage for item {top_parents[0]['parent_item']}" if top_parents else "Run master data validation check",
            "Run master data validation check",
            "Show planned orders and supply mix",
        ]

        return AgentResponse(
            agent="BomLineageAgent",
            workflow="BOM & Genealogy Lineage (V2)",
            summary=summary,
            confirmed_findings=findings,
            metrics={
                "total_bom_links": int(bom_agg.get("total_links") or 0),
                "parent_items": int(bom_agg.get("parent_items") or 0),
                "child_items": int(bom_agg.get("child_items") or 0),
                "alt_bom_rules": alt_cnt,
                "supersession_rules": sup_cnt,
            },
            rows=top_parents,
            evidence_sources=evidence,
            confidence="High",
            recommended_next_checks=next_checks,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
