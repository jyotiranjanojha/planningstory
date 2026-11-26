"""DuckDB-powered Supply Chain Lineage Knowledge Graph Agent (IFSP V2).

Builds 100% real lineage edges from BY ESP output tables:
  Resources -> Supply Methods -> Supply Plants -> Supply Items -> Demand Item -> Demand Locations
with SKU & Resource exception health overlays (healthy / warning / critical).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from ..core.data import PlanningDataEngine


def _safe_float(val: Any) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _scope_sql(
    item_col: str,
    loc_col: Optional[str],
    item_id: str,
    week_id: Optional[str],
    scenario_id: Optional[str],
    site: Optional[str],
    cols: Tuple[str, ...],
) -> Tuple[str, List[Any]]:
    col_set = {c.upper() for c in cols}
    clauses = [f"UPPER(TRIM({item_col})) = UPPER(TRIM(?))"]
    params: List[Any] = [item_id]
    if site and loc_col and loc_col.upper() in col_set:
        clauses.append(f"UPPER(TRIM({loc_col})) = UPPER(TRIM(?))")
        params.append(site)
    if week_id and "CAPTURE_WK" in col_set:
        clauses.append("TRIM(CAPTURE_WK) = TRIM(?)")
        params.append(week_id)
    if scenario_id and "SIMULATION_NAME" in col_set:
        clauses.append("UPPER(TRIM(SIMULATION_NAME)) = UPPER(TRIM(?))")
        params.append(scenario_id)
    return "WHERE " + " AND ".join(clauses), params


class KnowledgeGraphAgent:
    """Fast DuckDB-backed Knowledge Graph builder with real edges and bottleneck health."""

    DEMAND_TABLE = "by_if_snop_out_inddmdview"
    LINK_TABLE = "by_if_snop_out_inddmdlink"
    RES_LINK_TABLE = "by_if_snop_out_resloadinddmdlink"
    SKU_EXC_TABLE = "by_if_snop_out_skuexception"
    RES_EXC_TABLE = "by_if_snop_out_resexception"
    ITEM_TABLE = "if_snop_items"

    def __init__(self, data: PlanningDataEngine) -> None:
        self.data = data

    def list_selectable_items(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return demand items ranked by unmet qty with description & item class for UI picker."""
        if not self.data.has_table(self.DEMAND_TABLE):
            return []

        has_items = self.data.has_table(self.ITEM_TABLE)
        join_sql = (
            f'LEFT JOIN (SELECT DISTINCT TRIM(ITEM) AS ITEM, ANY_VALUE(DESCR) AS DESCR, ANY_VALUE(ITEMCLASS) AS ITEMCLASS FROM "{self.ITEM_TABLE}" GROUP BY 1) m ON UPPER(TRIM(d.ITEM)) = UPPER(m.ITEM)'
            if has_items
            else ""
        )
        desc_cols = ", COALESCE(m.DESCR, '') AS descr, COALESCE(m.ITEMCLASS, '') AS item_class" if has_items else ", '' AS descr, '' AS item_class"

        rows = self.data.query(
            f"""
            SELECT
                TRIM(d.ITEM) AS item_id
                {desc_cols},
                ROUND(COALESCE(SUM(TRY_CAST(d.QTY AS DOUBLE)), 0), 2) AS demand_qty,
                ROUND(COALESCE(SUM(TRY_CAST(d.SCHEDQTY AS DOUBLE)), 0), 2) AS scheduled_qty,
                ROUND(COALESCE(SUM(GREATEST(TRY_CAST(d.QTY AS DOUBLE) - COALESCE(TRY_CAST(d.SCHEDQTY AS DOUBLE), 0), 0)), 0), 2) AS unmet_qty
            FROM "{self.DEMAND_TABLE}" d
            {join_sql}
            WHERE TRIM(COALESCE(d.ITEM, '')) <> ''
            GROUP BY 1{', m.DESCR, m.ITEMCLASS' if has_items else ''}
            ORDER BY unmet_qty DESC, demand_qty DESC
            LIMIT ?
            """,
            [limit],
        )
        for r in rows:
            dmd = _safe_float(r.get("demand_qty"))
            sch = _safe_float(r.get("scheduled_qty"))
            r["fill_rate_pct"] = round((sch / dmd * 100.0) if dmd > 0 else 0.0, 1)
        return rows

    def build_graph(
        self,
        item_id: Optional[str],
        week_id: Optional[str] = None,
        scenario_id: Optional[str] = None,
        site: Optional[str] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        demand_item = (item_id or "").strip()
        site = (site or "").strip() or None
        week_id = (week_id or "").strip() or None
        scenario_id = (scenario_id or "").strip() or None

        if not demand_item or not self.data.has_table(self.DEMAND_TABLE):
            return {
                "Graph Scope": {"week_id": week_id, "scenario_id": scenario_id, "item_id": demand_item or None, "scope": {"site": site}},
                "Graph Summary": {
                    "demand_qty_total": 0.0,
                    "scheduled_qty_total": 0.0,
                    "unmet_qty": 0.0,
                    "fill_rate_pct": 0.0,
                    "status": "No Data",
                    "sku_exception_count": 0,
                    "resource_exception_count": 0,
                    "node_count": 0,
                    "edge_count": 0,
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                },
                "nodes": [],
                "edges": [],
            }

        # 1. Item master metadata
        item_descr = ""
        item_class = ""
        if self.data.has_table(self.ITEM_TABLE):
            im = self.data.query_one(
                f'SELECT DESCR, ITEMCLASS FROM "{self.ITEM_TABLE}" WHERE UPPER(TRIM(ITEM)) = UPPER(TRIM(?)) LIMIT 1',
                [demand_item],
            ) or {}
            item_descr = str(im.get("DESCR") or "").strip()
            item_class = str(im.get("ITEMCLASS") or "").strip()

        # 2. Demand KPIs & demand locations
        dmd_meta = self.data.table_meta(self.DEMAND_TABLE)
        dmd_where, dmd_params = _scope_sql("ITEM", "LOC", demand_item, week_id, scenario_id, site, dmd_meta.columns if dmd_meta else ())
        dmd_agg = self.data.query_one(
            f"""
            SELECT
                COUNT(*) AS demand_rows,
                COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0) AS demand_qty_total,
                COALESCE(SUM(TRY_CAST(SCHEDQTY AS DOUBLE)), 0) AS scheduled_qty_total,
                COALESCE(SUM(GREATEST(TRY_CAST(QTY AS DOUBLE) - COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0), 0)), 0) AS unmet_qty
            FROM "{self.DEMAND_TABLE}"
            {dmd_where}
            """,
            dmd_params,
        ) or {}

        demand_qty = _safe_float(dmd_agg.get("demand_qty_total"))
        sched_qty = _safe_float(dmd_agg.get("scheduled_qty_total"))
        unmet_qty = _safe_float(dmd_agg.get("unmet_qty"))
        fill_rate = (sched_qty / demand_qty * 100.0) if demand_qty > 0 else 0.0
        dmd_status = "Met" if (demand_qty > 0 and unmet_qty <= 0) else ("Unmet" if sched_qty <= 0 else "Partially Met")

        dmd_locs = self.data.query(
            f"""
            SELECT
                TRIM(LOC) AS loc,
                ROUND(COALESCE(SUM(TRY_CAST(QTY AS DOUBLE)), 0), 2) AS qty,
                ROUND(COALESCE(SUM(GREATEST(TRY_CAST(QTY AS DOUBLE) - COALESCE(TRY_CAST(SCHEDQTY AS DOUBLE), 0), 0)), 0), 2) AS unmet
            FROM "{self.DEMAND_TABLE}"
            {dmd_where}
            GROUP BY 1
            ORDER BY qty DESC
            LIMIT 6
            """,
            dmd_params,
        )

        # 3. Real supply lineage edges from inddmdlink
        supply_links: List[Dict[str, Any]] = []
        if self.data.has_table(self.LINK_TABLE):
            l_meta = self.data.table_meta(self.LINK_TABLE)
            l_where, l_params = _scope_sql("DMDITEM", "DMDLOC", demand_item, week_id, scenario_id, site, l_meta.columns if l_meta else ())
            supply_links = self.data.query(
                f"""
                SELECT
                    TRIM(COALESCE(SUPPLYITEM, '')) AS supply_item,
                    TRIM(COALESCE(SUPPLYLOC, '')) AS supply_loc,
                    TRIM(COALESCE(SUPPLYMETHOD, '')) AS supply_method,
                    TRIM(COALESCE(SUPPLYTYPE, '')) AS supply_type,
                    ROUND(COALESCE(SUM(TRY_CAST(SUPPLYPEGQTY AS DOUBLE)), 0), 2) AS pegged_qty
                FROM "{self.LINK_TABLE}"
                {l_where}
                GROUP BY 1, 2, 3, 4
                ORDER BY pegged_qty DESC
                LIMIT 20
                """,
                l_params,
            )

        # 4. Real resource -> method / supply_item edges from resloadinddmdlink
        res_links: List[Dict[str, Any]] = []
        if self.data.has_table(self.RES_LINK_TABLE):
            r_meta = self.data.table_meta(self.RES_LINK_TABLE)
            r_cols = {c.upper() for c in (r_meta.columns if r_meta else ())}
            r_where, r_params = _scope_sql("DMDITEM", "DMDLOC", demand_item, week_id, scenario_id, site, r_meta.columns if r_meta else ())
            cap_col = "CAPACITYPEGQTY" if "CAPACITYPEGQTY" in r_cols else ("LOADQTY" if "LOADQTY" in r_cols else "0")
            res_links = self.data.query(
                f"""
                SELECT
                    TRIM(COALESCE(RES, '')) AS res,
                    TRIM(COALESCE(SUPPLYMETHOD, '')) AS supply_method,
                    TRIM(COALESCE(SUPPLYITEM, '')) AS supply_item,
                    TRIM(COALESCE(SUPPLYLOC, '')) AS supply_loc,
                    ROUND(COALESCE(SUM(TRY_CAST({cap_col} AS DOUBLE)), 0), 2) AS cap_qty
                FROM "{self.RES_LINK_TABLE}"
                {r_where}
                GROUP BY 1, 2, 3, 4
                ORDER BY cap_qty DESC
                LIMIT 20
                """,
                r_params,
            )

        # 5. Exception overlays (SKU exceptions & Resource exceptions)
        involved_items = {demand_item} | {r["supply_item"] for r in supply_links if r.get("supply_item")}
        involved_resources = {r["res"] for r in res_links if r.get("res")}

        sku_exc_by_item: Dict[str, Dict[str, Any]] = {}
        total_sku_exc = 0
        if self.data.has_table(self.SKU_EXC_TABLE) and involved_items:
            ph = ", ".join(["UPPER(TRIM(?))"] * len(involved_items))
            exc_rows = self.data.query(
                f"""
                SELECT
                    TRIM(ITEM) AS item,
                    COUNT(*) AS exc_count,
                    ANY_VALUE(COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'SKU Exception')) AS top_reason
                FROM "{self.SKU_EXC_TABLE}"
                WHERE UPPER(TRIM(ITEM)) IN ({ph})
                GROUP BY 1
                """,
                list(involved_items),
            )
            for r in exc_rows:
                cnt = int(r.get("exc_count") or 0)
                total_sku_exc += cnt
                sku_exc_by_item[str(r["item"]).upper()] = {"count": cnt, "top_reason": r.get("top_reason") or ""}

        res_exc_by_res: Dict[str, Dict[str, Any]] = {}
        total_res_exc = 0
        if self.data.has_table(self.RES_EXC_TABLE) and involved_resources:
            ph = ", ".join(["UPPER(TRIM(?))"] * len(involved_resources))
            rexc_rows = self.data.query(
                f"""
                SELECT
                    TRIM(RES) AS res,
                    COUNT(*) AS exc_count,
                    ANY_VALUE(COALESCE(NULLIF(TRIM(DESCR), ''), NULLIF(TRIM(EXCEPTION), ''), 'Resource Exception')) AS top_reason
                FROM "{self.RES_EXC_TABLE}"
                WHERE UPPER(TRIM(RES)) IN ({ph})
                GROUP BY 1
                """,
                list(involved_resources),
            )
            for r in rexc_rows:
                cnt = int(r.get("exc_count") or 0)
                total_res_exc += cnt
                res_exc_by_res[str(r["res"]).upper()] = {"count": cnt, "top_reason": r.get("top_reason") or ""}

        # 6. Assemble clean graph nodes & real edges
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        seen_nodes: set[str] = set()
        seen_edges: set[Tuple[str, str, str]] = set()

        def add_node(node_id: str, label: str, node_type: str, meta: Optional[Dict[str, Any]] = None) -> None:
            if not node_id or node_id in seen_nodes:
                return
            seen_nodes.add(node_id)
            nodes.append({"id": node_id, "label": label, "type": node_type, "meta": meta or {}})

        def add_edge(source: str, target: str, label: str, value: float = 0.0) -> None:
            key = (source, target, label)
            if not source or not target or key in seen_edges:
                return
            seen_edges.add(key)
            edges.append({"source": source, "target": target, "label": label, "value": round(value, 2)})

        # Demand Item node
        dmd_exc = sku_exc_by_item.get(demand_item.upper(), {})
        dmd_health = "critical" if (unmet_qty > 0 or dmd_exc.get("count", 0) > 0) else "healthy"
        demand_node_id = f"demand:{demand_item}"
        add_node(
            demand_node_id,
            demand_item,
            "demand_item",
            {
                "description": item_descr or None,
                "item_class": item_class or None,
                "demand_qty": round(demand_qty, 2),
                "scheduled_qty": round(sched_qty, 2),
                "unmet_qty": round(unmet_qty, 2),
                "fill_rate_pct": round(fill_rate, 1),
                "status": dmd_status,
                "exception_count": dmd_exc.get("count", 0),
                "top_exception": dmd_exc.get("top_reason") or None,
                "health": dmd_health,
            },
        )

        # Demand Location nodes (rightmost lane: Demand Item -> Demand Location)
        for loc_row in dmd_locs:
            loc = loc_row.get("loc")
            if not loc:
                continue
            loc_unmet = _safe_float(loc_row.get("unmet"))
            loc_id = f"loc:{loc}"
            add_node(
                loc_id,
                loc,
                "location",
                {
                    "demand_qty": _safe_float(loc_row.get("qty")),
                    "unmet_qty": loc_unmet,
                    "health": "warning" if loc_unmet > 0 else "healthy",
                },
            )
            add_edge(demand_node_id, loc_id, "demand at", _safe_float(loc_row.get("qty")))

        # Supply Items, Supply Locations, and Supply Methods (real edges from inddmdlink)
        supply_item_nodes: Dict[Tuple[str, str], str] = {}
        method_nodes: set[str] = set()

        for row in supply_links[:12]:
            s_item = row["supply_item"]
            s_loc = row["supply_loc"]
            s_method = row["supply_method"]
            qty = _safe_float(row["pegged_qty"])
            if not s_item:
                continue

            s_node_id = f"supply-item:{s_item}:{s_loc or 'na'}"
            supply_item_nodes[(s_item, s_loc)] = s_node_id
            s_exc = sku_exc_by_item.get(s_item.upper(), {})
            add_node(
                s_node_id,
                s_item,
                "supply_item",
                {
                    "location": s_loc or None,
                    "supply_type": row.get("supply_type") or None,
                    "pegged_qty": qty,
                    "exception_count": s_exc.get("count", 0),
                    "top_exception": s_exc.get("top_reason") or None,
                    "health": "critical" if s_exc.get("count", 0) > 0 else "healthy",
                },
            )
            add_edge(s_node_id, demand_node_id, "pegs to", qty)

            if s_loc:
                sloc_id = f"supply-loc:{s_loc}"
                add_node(sloc_id, s_loc, "supply_location", {"plant": s_loc, "health": "healthy"})
                add_edge(sloc_id, s_node_id, "supplies from", qty)

            if s_method:
                m_id = f"method:{s_method}"
                method_nodes.add(m_id)
                add_node(m_id, s_method, "supply_method", {"pegged_qty": qty, "health": "healthy"})
                add_edge(m_id, s_node_id, "executes", qty)

        # Resources (real edges from resloadinddmdlink)
        for row in res_links[:10]:
            res = row["res"]
            s_method = row["supply_method"]
            s_item = row["supply_item"]
            s_loc = row["supply_loc"]
            qty = _safe_float(row["cap_qty"])
            if not res:
                continue

            res_id = f"resource:{res}"
            r_exc = res_exc_by_res.get(res.upper(), {})
            add_node(
                res_id,
                res,
                "resource",
                {
                    "capacity_pegged_qty": qty,
                    "exception_count": r_exc.get("count", 0),
                    "top_exception": r_exc.get("top_reason") or None,
                    "health": "critical" if r_exc.get("count", 0) > 0 else "healthy",
                },
            )
            if s_method:
                m_id = f"method:{s_method}"
                if m_id not in seen_nodes:
                    add_node(m_id, s_method, "supply_method", {"pegged_qty": qty, "health": "healthy"})
                add_edge(res_id, m_id, "loads", qty)
            elif s_item:
                s_node_id = supply_item_nodes.get((s_item, s_loc)) or f"supply-item:{s_item}:{s_loc or 'na'}"
                if s_node_id in seen_nodes:
                    add_edge(res_id, s_node_id, "loads", qty)

        duration_ms = int((time.perf_counter() - t0) * 1000)

        return {
            "Graph Scope": {
                "week_id": week_id,
                "scenario_id": scenario_id,
                "item_id": demand_item,
                "item_descr": item_descr or None,
                "item_class": item_class or None,
                "scope": {"site": site},
            },
            "Graph Summary": {
                "demand_qty_total": round(demand_qty, 2),
                "scheduled_qty_total": round(sched_qty, 2),
                "unmet_qty": round(unmet_qty, 2),
                "fill_rate_pct": round(fill_rate, 1),
                "status": dmd_status,
                "sku_exception_count": total_sku_exc,
                "resource_exception_count": total_res_exc,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "duration_ms": duration_ms,
            },
            "nodes": nodes,
            "edges": edges,
        }
