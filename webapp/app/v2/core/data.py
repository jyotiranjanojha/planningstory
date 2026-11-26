"""DuckDB-first BY ESP Data Engine for IFSP V2 Core.

Registers all by_input/*.csv and by_output/*.csv snapshots once as DuckDB views
with clean canonical table names (timestamps stripped), enabling <50ms SQL
aggregations, joins, and drill-downs across the entire planning snapshot.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

logger = logging.getLogger(__name__)

_TIMESTAMP_SUFFIX_RE = re.compile(r"-\d{8,14}$")


@dataclass(frozen=True)
class TableMeta:
    name: str
    family: str  # "by_input" | "by_output"
    file_path: str
    delimiter: str
    columns: Tuple[str, ...]


def _canonical_table_name(file_path: Path) -> str:
    stem = _TIMESTAMP_SUFFIX_RE.sub("", file_path.stem)
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", stem).lower().strip("_")
    return clean


def _detect_delimiter(file_path: Path) -> str:
    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        header = f.readline()
    return "|" if header.count("|") >= header.count(",") else ","


def _read_header(file_path: Path, delimiter: str) -> Tuple[str, ...]:
    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        header = f.readline().strip()
    return tuple(c.strip() for c in header.split(delimiter) if c.strip())


class PlanningDataEngine:
    """Thread-safe in-memory DuckDB engine over BY ESP snapshots."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self._lock = threading.RLock()
        self._conn = duckdb.connect(database=":memory:")
        self._tables: Dict[str, TableMeta] = {}
        self.refresh_catalog()

    def refresh_catalog(self) -> int:
        """Discover and register all CSVs in by_input/ and by_output/ as DuckDB views."""
        with self._lock:
            self._tables.clear()
            for family in ("by_input", "by_output"):
                folder = self.base_dir / family
                if not folder.exists():
                    continue
                for csv_path in sorted(folder.glob("*.csv")):
                    table_name = _canonical_table_name(csv_path)
                    delim = _detect_delimiter(csv_path)
                    cols = _read_header(csv_path, delim)
                    escaped_path = str(csv_path).replace("'", "''")
                    escaped_delim = delim.replace("'", "''")
                    try:
                        self._conn.execute(
                            f"""
                            CREATE OR REPLACE VIEW "{table_name}" AS
                            SELECT * FROM read_csv(
                                '{escaped_path}',
                                delim='{escaped_delim}',
                                header=true,
                                all_varchar=true,
                                ignore_errors=true,
                                null_padding=true
                            )
                            """
                        )
                        self._tables[table_name] = TableMeta(
                            name=table_name,
                            family=family,
                            file_path=str(csv_path),
                            delimiter=delim,
                            columns=cols,
                        )
                    except Exception as exc:
                        logger.warning("Failed to register view %s from %s: %s", table_name, csv_path.name, exc)
            logger.info("PlanningDataEngine registered %d DuckDB views from %s", len(self._tables), self.base_dir)
            return len(self._tables)

    def has_table(self, name: str) -> bool:
        return name.lower() in self._tables

    def table_meta(self, name: str) -> Optional[TableMeta]:
        return self._tables.get(name.lower())

    def catalog_summary(self) -> Dict[str, Any]:
        by_in = [t for t in self._tables.values() if t.family == "by_input"]
        by_out = [t for t in self._tables.values() if t.family == "by_output"]
        return {
            "base_dir": str(self.base_dir),
            "total_views": len(self._tables),
            "by_input_count": len(by_in),
            "by_output_count": len(by_out),
            "by_input": [{"table": t.name, "columns": len(t.columns), "file": Path(t.file_path).name} for t in by_in],
            "by_output": [{"table": t.name, "columns": len(t.columns), "file": Path(t.file_path).name} for t in by_out],
        }

    def query(self, sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """Execute a read-only SQL query and return list of row dicts."""
        with self._lock:
            cursor = self._conn.execute(sql, params or [])
            cols = [desc[0] for desc in (cursor.description or [])]
            rows = cursor.fetchall()
            return [dict(zip(cols, row)) for row in rows]

    def query_one(self, sql: str, params: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None


_ENGINE_LOCK = threading.Lock()
_ENGINE_CACHE: Dict[str, PlanningDataEngine] = {}


def get_data_engine(base_dir: Path) -> PlanningDataEngine:
    key = str(Path(base_dir).resolve())
    with _ENGINE_LOCK:
        engine = _ENGINE_CACHE.get(key)
        if engine is None:
            engine = PlanningDataEngine(Path(base_dir))
            _ENGINE_CACHE[key] = engine
        return engine
