"""DuckDB-first BY ESP Data Engine for IFSP V2 Core.

Registers all by_input/*.csv and by_output/*.csv snapshots once as DuckDB views
with clean canonical table names (timestamps stripped), enabling <50ms SQL
aggregations, joins, and drill-downs across the entire planning snapshot.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

logger = logging.getLogger(__name__)

_TIMESTAMP_SUFFIX_RE = re.compile(r"-\d{8,14}$")
_SYNC_LOCK = threading.RLock()
_SYNCED_SOURCES: Dict[str, Tuple[Path, Path]] = {}


def _resolve_path(raw: str, base_dir: Path) -> Path:
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        p = base_dir / p
    return p


def _is_remote_unc_path(path: Path) -> bool:
    s = str(path)
    return s.startswith("\\\\") or s.startswith("//")


def _cache_enabled_for(input_dir: Path, output_dir: Path) -> bool:
    mode = os.getenv("PLANNING_DATA_CACHE_ENABLED", "auto").strip().lower()
    if mode in {"1", "true", "yes", "on"}:
        return True
    if mode in {"0", "false", "no", "off"}:
        return False
    # 'auto': enable local snapshot cache automatically for UNC/SMB network shares
    return _is_remote_unc_path(input_dir) or _is_remote_unc_path(output_dir)


def _get_cache_root(base_dir: Path) -> Path:
    raw = os.getenv("PLANNING_DATA_CACHE_DIR", "").strip()
    if raw:
        return _resolve_path(raw, base_dir)
    return Path(base_dir) / "data" / "snapshot_cache"


def resolve_source_data_dirs(base_dir: Path) -> Tuple[Path, Path]:
    """Resolve raw (un-cached) BY ESP input and output source directories.

    Resolution order:
    1. Explicit ``BY_INPUT_DIR`` / ``BY_OUTPUT_DIR`` environment variables.
    2. Shared root ``PLANNING_DATA_DIR`` (expects ``by_input/`` and ``by_output/`` subfolders).
    3. Fallback to ``<base_dir>/by_input`` and ``<base_dir>/by_output``.
    """
    base = Path(base_dir)
    shared_root_raw = os.getenv("PLANNING_DATA_DIR", "").strip()
    root = _resolve_path(shared_root_raw, base) if shared_root_raw else base

    in_raw = os.getenv("BY_INPUT_DIR", "").strip()
    out_raw = os.getenv("BY_OUTPUT_DIR", "").strip()

    input_dir = _resolve_path(in_raw, base) if in_raw else (root / "by_input")
    output_dir = _resolve_path(out_raw, base) if out_raw else (root / "by_output")
    return input_dir, output_dir


def _sync_folder_incremental(src_dir: Path, dst_dir: Path, max_workers: int = 8) -> Dict[str, Any]:
    """Incrementally mirror *.csv files from src_dir to local dst_dir in parallel.

    Skips files whose (size, mtime) match; copies changed files atomically via .tmp;
    preserves existing cache if src_dir is temporarily unreachable.
    """
    t0 = time.monotonic()
    dst_dir.mkdir(parents=True, exist_ok=True)
    if not src_dir.exists():
        logger.warning("Source data directory unreachable: %s (using existing cache at %s)", src_dir, dst_dir)
        return {"copied": 0, "skipped": 0, "removed": 0, "reachable": False, "duration_ms": int((time.monotonic() - t0) * 1000)}

    src_entries: List[Tuple[str, Path, int, float]] = []
    with os.scandir(src_dir) as it:
        for entry in it:
            if entry.is_file() and entry.name.lower().endswith(".csv"):
                st = entry.stat()
                src_entries.append((entry.name, Path(entry.path), st.st_size, st.st_mtime))

    if not src_entries:
        logger.warning("No CSV files found in %s; keeping existing cache at %s", src_dir, dst_dir)
        return {"copied": 0, "skipped": 0, "removed": 0, "reachable": True, "duration_ms": int((time.monotonic() - t0) * 1000)}

    to_copy: List[Tuple[Path, Path, float]] = []
    skipped = 0
    src_names = set()
    for name, src_path, size, mtime in src_entries:
        src_names.add(name)
        dst_path = dst_dir / name
        if dst_path.exists():
            dst_st = dst_path.stat()
            if dst_st.st_size == size and abs(dst_st.st_mtime - mtime) < 2.0:
                skipped += 1
                continue
        to_copy.append((src_path, dst_path, mtime))

    def _copy_one(item: Tuple[Path, Path, float]) -> None:
        src_p, dst_p, mtime = item
        tmp_p = dst_p.with_suffix(dst_p.suffix + ".tmp")
        shutil.copyfile(src_p, tmp_p)
        os.utime(tmp_p, (mtime, mtime))
        os.replace(tmp_p, dst_p)

    if to_copy:
        workers = max(1, min(max_workers, len(to_copy)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_copy_one, to_copy))

    removed = 0
    for existing in dst_dir.glob("*.csv"):
        if existing.name not in src_names:
            try:
                existing.unlink()
                removed += 1
            except OSError:
                pass

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "Synced %s -> %s (copied=%d, skipped=%d, removed=%d, %dms)",
        src_dir, dst_dir, len(to_copy), skipped, removed, elapsed_ms,
    )
    return {
        "copied": len(to_copy),
        "skipped": skipped,
        "removed": removed,
        "reachable": True,
        "duration_ms": elapsed_ms,
    }


def sync_data_cache(base_dir: Path, force: bool = False) -> Dict[str, Any]:
    """Sync remote/shared BY ESP directories into the local snapshot cache if enabled."""
    src_in, src_out = resolve_source_data_dirs(base_dir)
    if not _cache_enabled_for(src_in, src_out):
        return {
            "cache_enabled": False,
            "input_dir": str(src_in),
            "output_dir": str(src_out),
            "source_input_dir": str(src_in),
            "source_output_dir": str(src_out),
        }

    cache_root = _get_cache_root(base_dir)
    cache_in = cache_root / "by_input"
    cache_out = cache_root / "by_output"
    cache_key = f"{src_in}|{src_out}|{cache_root}"

    with _SYNC_LOCK:
        if force or cache_key not in _SYNCED_SOURCES:
            in_stats = _sync_folder_incremental(src_in, cache_in)
            out_stats = _sync_folder_incremental(src_out, cache_out)
            _SYNCED_SOURCES[cache_key] = (cache_in, cache_out)
        else:
            in_stats = {"copied": 0, "skipped": -1, "removed": 0, "reachable": True, "duration_ms": 0}
            out_stats = {"copied": 0, "skipped": -1, "removed": 0, "reachable": True, "duration_ms": 0}

    return {
        "cache_enabled": True,
        "input_dir": str(cache_in),
        "output_dir": str(cache_out),
        "source_input_dir": str(src_in),
        "source_output_dir": str(src_out),
        "by_input_sync": in_stats,
        "by_output_sync": out_stats,
    }


def resolve_data_dirs(base_dir: Path) -> Tuple[Path, Path]:
    """Resolve effective BY ESP input and output directories.

    When reading from a UNC/SMB network share (or when ``PLANNING_DATA_CACHE_ENABLED=true``),
    automatically mirrors CSVs incrementally to a fast local snapshot cache so DuckDB,
    analyzer, and RAG operate at sub-second speed.
    """
    src_in, src_out = resolve_source_data_dirs(base_dir)
    if not _cache_enabled_for(src_in, src_out):
        return src_in, src_out
    sync_data_cache(base_dir, force=False)
    cache_root = _get_cache_root(base_dir)
    return cache_root / "by_input", cache_root / "by_output"


def resolve_input_dir(base_dir: Path) -> Path:
    return resolve_data_dirs(base_dir)[0]


def resolve_output_dir(base_dir: Path) -> Path:
    return resolve_data_dirs(base_dir)[1]


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


def _get_refresh_interval_secs() -> int:
    raw = os.getenv("PLANNING_DATA_REFRESH_INTERVAL_SECS", "300").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _dir_csv_fingerprint(folder: Path) -> Tuple[Tuple[str, int, int], ...]:
    """Return sorted (name, size, int_mtime) snapshot for all *.csv files in folder."""
    if not folder.exists():
        return ()
    entries: List[Tuple[str, int, int]] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_file() and entry.name.lower().endswith(".csv"):
                    st = entry.stat()
                    entries.append((entry.name, st.st_size, int(st.st_mtime)))
    except OSError:
        return ()
    entries.sort()
    return tuple(entries)


class PlanningDataEngine:
    """Thread-safe in-memory DuckDB engine over BY ESP snapshots with zero-touch auto-refresh."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.source_input_dir, self.source_output_dir = resolve_source_data_dirs(self.base_dir)
        self.cache_enabled = _cache_enabled_for(self.source_input_dir, self.source_output_dir)
        self.refresh_interval_secs = _get_refresh_interval_secs()
        self.input_dir, self.output_dir = resolve_data_dirs(self.base_dir)
        self._lock = threading.RLock()
        self._conn = duckdb.connect(database=":memory:")
        self._tables: Dict[str, TableMeta] = {}
        self._last_fingerprint: Tuple[Tuple[Tuple[str, int, int], ...], Tuple[Tuple[str, int, int], ...]] = ((), ())
        self.last_checked_at: Optional[str] = None
        self.last_refreshed_at: Optional[str] = None
        self.refresh_catalog(force_sync=False)
        self._start_auto_refresh_thread()

    def _start_auto_refresh_thread(self) -> None:
        interval = self.refresh_interval_secs
        if interval <= 0:
            return

        def _loop() -> None:
            while True:
                time.sleep(interval)
                try:
                    self.check_for_updates()
                except Exception as exc:
                    logger.warning("Auto-refresh check failed (keeping cached views): %s", exc)

        t = threading.Thread(target=_loop, name="ifsp-data-auto-refresh", daemon=True)
        t.start()
        logger.info("PlanningDataEngine auto-refresh enabled (every %ds)", interval)

    def check_for_updates(self) -> Dict[str, Any]:
        """Check source folders for new/modified CSVs; reload DuckDB views only if changed."""
        now_iso = datetime.now(timezone.utc).isoformat()
        self.last_checked_at = now_iso
        src_in, src_out = resolve_source_data_dirs(self.base_dir)
        if _cache_enabled_for(src_in, src_out):
            sync_info = sync_data_cache(self.base_dir, force=True)
            in_s = sync_info.get("by_input_sync", {})
            out_s = sync_info.get("by_output_sync", {})
            copied = int(in_s.get("copied", 0)) + int(out_s.get("copied", 0))
            removed = int(in_s.get("removed", 0)) + int(out_s.get("removed", 0))
            skipped = int(in_s.get("skipped", 0)) + int(out_s.get("skipped", 0))
            changed = (copied > 0) or (removed > 0)
            if changed:
                views = self.refresh_catalog(force_sync=False)
                logger.info(
                    "Auto-refresh detected changes (copied=%d, removed=%d, skipped=%d); reloaded %d views",
                    copied, removed, skipped, views,
                )
            return {
                "changed": changed,
                "copied": copied,
                "removed": removed,
                "skipped": skipped,
                "checked_at": now_iso,
                "total_views": len(self._tables),
            }

        # Non-cached mode (local folder or fast PVC): compare directory fingerprints
        new_fp = (_dir_csv_fingerprint(src_in), _dir_csv_fingerprint(src_out))
        changed = new_fp != self._last_fingerprint
        if changed:
            views = self.refresh_catalog(force_sync=False)
            logger.info("Auto-refresh detected local file changes; reloaded %d views", views)
        return {
            "changed": changed,
            "copied": 0,
            "removed": 0,
            "skipped": 0,
            "checked_at": now_iso,
            "total_views": len(self._tables),
        }

    def refresh_catalog(self, force_sync: bool = False) -> int:
        """Discover and register all CSVs in the resolved input and output directories as DuckDB views."""
        with self._lock:
            self._tables.clear()
            self.source_input_dir, self.source_output_dir = resolve_source_data_dirs(self.base_dir)
            self.cache_enabled = _cache_enabled_for(self.source_input_dir, self.source_output_dir)
            if force_sync and self.cache_enabled:
                sync_data_cache(self.base_dir, force=True)
            self.input_dir, self.output_dir = resolve_data_dirs(self.base_dir)
            for family, folder in (("by_input", self.input_dir), ("by_output", self.output_dir)):
                if not folder.exists():
                    logger.warning("Data directory for %s does not exist: %s", family, folder)
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
            self._last_fingerprint = (_dir_csv_fingerprint(self.input_dir), _dir_csv_fingerprint(self.output_dir))
            now_iso = datetime.now(timezone.utc).isoformat()
            self.last_refreshed_at = now_iso
            self.last_checked_at = now_iso
            logger.info(
                "PlanningDataEngine registered %d DuckDB views (input=%s, output=%s)",
                len(self._tables),
                self.input_dir,
                self.output_dir,
            )
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
            "input_dir": str(self.input_dir),
            "output_dir": str(self.output_dir),
            "source_input_dir": str(self.source_input_dir),
            "source_output_dir": str(self.source_output_dir),
            "cache_enabled": self.cache_enabled,
            "refresh_interval_secs": self.refresh_interval_secs,
            "last_checked_at": self.last_checked_at,
            "last_refreshed_at": self.last_refreshed_at,
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
    resolved_base = Path(base_dir).resolve()
    in_dir, out_dir = resolve_data_dirs(resolved_base)
    key = f"{resolved_base}|{in_dir}|{out_dir}"
    with _ENGINE_LOCK:
        engine = _ENGINE_CACHE.get(key)
        if engine is None:
            engine = PlanningDataEngine(resolved_base)
            _ENGINE_CACHE[key] = engine
        return engine
