"""近重复侧车：SQLite/JSONL，存 path_prefix_id + 相对路径。"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from auto_tag.core.path_prefix_registry import PathPrefixRegistry

logger = logging.getLogger(__name__)


def _is_sqlite_path(file_path: str) -> bool:
    return str(file_path).lower().endswith(".sqlite") or str(file_path).lower().endswith(
        ".db"
    )


def _duplicate_links_table_columns(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='duplicate_links'"
    )
    if not cur.fetchone():
        return set()
    return {r[1] for r in conn.execute("PRAGMA table_info(duplicate_links)")}


def _compose_row(registry: PathPrefixRegistry, row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if row.get("anchor_prefix_id") is not None:
        ap = registry.compose(str(row["anchor_prefix_id"]), str(row.get("anchor_rel_path") or ""))
        dp = registry.compose(str(row["dup_prefix_id"]), str(row.get("dup_rel_path") or ""))
    else:
        ap = str(row.get("anchor_path") or "")
        dp = str(row.get("dup_path") or "")
    out["anchor_path"] = ap
    out["dup_path"] = dp
    return out


class DuplicateLinkWriter:
    def __init__(
        self,
        log_dir: str,
        registry: PathPrefixRegistry,
        filename: str = "duplicate_links.sqlite",
    ):
        os.makedirs(log_dir, exist_ok=True)
        self._path = os.path.join(log_dir, filename)
        self._registry = registry
        self._lock = threading.Lock()
        self._use_sqlite = _is_sqlite_path(self._path)
        if self._use_sqlite:
            self._init_sqlite()

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self._path) as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='duplicate_links'"
            )
            if cur.fetchone():
                cols = {
                    r[1] for r in conn.execute("PRAGMA table_info(duplicate_links)")
                }
                if "dup_rel_path" not in cols:
                    conn.execute("DROP TABLE duplicate_links")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS duplicate_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    anchor_id TEXT NOT NULL,
                    anchor_prefix_id TEXT NOT NULL,
                    anchor_rel_path TEXT NOT NULL,
                    dup_prefix_id TEXT NOT NULL,
                    dup_rel_path TEXT NOT NULL,
                    distance REAL NOT NULL,
                    ts TEXT NOT NULL
                )
                """
            )
            conn.commit()

    @property
    def path(self) -> str:
        return self._path

    def append(
        self,
        anchor_id: str,
        anchor_path: str,
        dup_path: str,
        distance: float,
    ) -> None:
        ap_id, ap_rel = self._registry.split(anchor_path or "")
        dp_id, dp_rel = self._registry.split(dup_path)
        ts = datetime.now(timezone.utc).isoformat()
        if self._use_sqlite:
            try:
                with self._lock:
                    with sqlite3.connect(self._path) as conn:
                        conn.execute(
                            """
                            INSERT INTO duplicate_links
                            (anchor_id, anchor_prefix_id, anchor_rel_path,
                             dup_prefix_id, dup_rel_path, distance, ts)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                anchor_id,
                                str(ap_id),
                                ap_rel,
                                str(dp_id),
                                dp_rel,
                                float(distance),
                                ts,
                            ),
                        )
                        conn.commit()
            except OSError as e:
                logger.error("Failed to append duplicate link (sqlite): %s", e)
            return

        record: Dict[str, Any] = {
            "anchor_id": anchor_id,
            "anchor_prefix_id": str(ap_id),
            "anchor_rel_path": ap_rel,
            "dup_prefix_id": str(dp_id),
            "dup_rel_path": dp_rel,
            "distance": float(distance),
            "ts": ts,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
        except OSError as e:
            logger.error("Failed to append duplicate link record: %s", e)


def find_duplicate_links_for_paths(
    store_path: str,
    path_variants: List[str],
    *,
    log_dir: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    if not path_variants or not os.path.isfile(store_path):
        return []
    variants = [str(v).strip() for v in path_variants if str(v).strip()]
    if not variants:
        return []
    ld = log_dir or os.path.dirname(os.path.abspath(store_path))
    registry = PathPrefixRegistry(ld)

    if _is_sqlite_path(store_path):
        try:
            with sqlite3.connect(store_path) as conn:
                conn.row_factory = sqlite3.Row
                cols = _duplicate_links_table_columns(conn)
                if not cols:
                    return []
                if "dup_rel_path" in cols:
                    sql = """
                    SELECT anchor_id, anchor_prefix_id, anchor_rel_path,
                           dup_prefix_id, dup_rel_path, distance, ts
                    FROM duplicate_links
                    ORDER BY id DESC
                    LIMIT 50000
                    """
                elif "anchor_path" in cols and "dup_path" in cols:
                    sql = """
                    SELECT anchor_id, anchor_path, dup_path, distance, ts
                    FROM duplicate_links
                    ORDER BY id DESC
                    LIMIT 50000
                    """
                else:
                    logger.warning(
                        "duplicate_links 表结构未知，跳过查找: %s", store_path
                    )
                    return []
                cur = conn.execute(sql)
                out: List[Dict[str, Any]] = []
                for r in cur.fetchall():
                    if "dup_rel_path" in cols:
                        row = {
                            "anchor_id": r["anchor_id"],
                            "anchor_prefix_id": r["anchor_prefix_id"],
                            "anchor_rel_path": r["anchor_rel_path"] or "",
                            "dup_prefix_id": r["dup_prefix_id"],
                            "dup_rel_path": r["dup_rel_path"] or "",
                            "distance": float(r["distance"]),
                            "ts": r["ts"],
                        }
                    else:
                        row = {
                            "anchor_id": r["anchor_id"],
                            "anchor_path": r["anchor_path"] or "",
                            "dup_path": r["dup_path"] or "",
                            "distance": float(r["distance"]),
                            "ts": r["ts"],
                        }
                    cr = _compose_row(registry, row)
                    af = cr["anchor_path"]
                    df = cr["dup_path"]
                    if any(v == af or v == df for v in variants):
                        out.append(cr)
                        if len(out) >= limit:
                            break
                return out
        except (OSError, sqlite3.Error) as e:
            logger.error("find_duplicate_links_for_paths sqlite: %s", e)
            return []

    out: List[Dict[str, Any]] = []
    try:
        with open(store_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cr = _compose_row(registry, row)
                ap = str(cr.get("anchor_path") or "")
                dp = str(cr.get("dup_path") or "")
                if any(v == ap or v == dp for v in variants):
                    out.append(cr)
                    if len(out) >= limit:
                        break
    except OSError as e:
        logger.error("find_duplicate_links_for_paths jsonl: %s", e)
    return out


def load_all_duplicate_rows(
    file_path: str, *, log_dir: Optional[str] = None
) -> List[Dict[str, Any]]:
    """读取侧车全部行（用于导出等；大文件会占内存）。"""
    if not os.path.isfile(file_path):
        return []
    out: List[Dict[str, Any]] = []
    off = 0
    batch = 50_000
    while True:
        chunk, total = read_duplicate_store(
            file_path, limit=batch, offset=off, log_dir=log_dir
        )
        out.extend(chunk)
        if not chunk or off + len(chunk) >= total:
            break
        off += len(chunk)
    return out


def read_duplicate_store(
    file_path: str,
    *,
    limit: int = 100,
    offset: int = 0,
    log_dir: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    ld = log_dir or os.path.dirname(os.path.abspath(file_path))
    if _is_sqlite_path(file_path):
        return _read_sqlite(file_path, limit=limit, offset=offset, log_dir=ld)
    return read_duplicate_links_jsonl(file_path, limit=limit, offset=offset, log_dir=ld)


def _read_sqlite(
    file_path: str, *, limit: int, offset: int, log_dir: str
) -> Tuple[List[Dict[str, Any]], int]:
    if not os.path.isfile(file_path):
        return [], 0
    registry = PathPrefixRegistry(log_dir)
    try:
        with sqlite3.connect(file_path) as conn:
            conn.row_factory = sqlite3.Row
            cols = _duplicate_links_table_columns(conn)
            if not cols:
                return [], 0
            cur = conn.execute("SELECT COUNT(*) FROM duplicate_links")
            total = int(cur.fetchone()[0])
            if "dup_rel_path" in cols:
                cur = conn.execute(
                    """
                    SELECT anchor_id, anchor_prefix_id, anchor_rel_path,
                           dup_prefix_id, dup_rel_path, distance, ts
                    FROM duplicate_links
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
                rows = []
                for r in cur.fetchall():
                    row = {
                        "anchor_id": r["anchor_id"],
                        "anchor_prefix_id": r["anchor_prefix_id"],
                        "anchor_rel_path": r["anchor_rel_path"] or "",
                        "dup_prefix_id": r["dup_prefix_id"],
                        "dup_rel_path": r["dup_rel_path"] or "",
                        "distance": float(r["distance"]),
                        "ts": r["ts"],
                    }
                    rows.append(_compose_row(registry, row))
                return rows, total
            if "anchor_path" in cols and "dup_path" in cols:
                cur = conn.execute(
                    """
                    SELECT anchor_id, anchor_path, dup_path, distance, ts
                    FROM duplicate_links
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
                rows = []
                for r in cur.fetchall():
                    row = {
                        "anchor_id": r["anchor_id"],
                        "anchor_path": r["anchor_path"] or "",
                        "dup_path": r["dup_path"] or "",
                        "distance": float(r["distance"]),
                        "ts": r["ts"],
                    }
                    rows.append(_compose_row(registry, row))
                return rows, total
            logger.warning("duplicate_links 表结构未知，无法读取: %s", file_path)
            return [], 0
    except (OSError, sqlite3.Error) as e:
        logger.error("Failed to read duplicate sqlite: %s", e)
        return [], 0


def read_duplicate_links_jsonl(
    file_path: str,
    *,
    limit: int = 100,
    offset: int = 0,
    log_dir: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    if not os.path.isfile(file_path):
        return [], 0
    ld = log_dir or os.path.dirname(os.path.abspath(file_path))
    registry = PathPrefixRegistry(ld)
    all_rows: List[Dict[str, Any]] = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    all_rows.append(_compose_row(registry, raw))
                except json.JSONDecodeError:
                    logger.warning("Skip bad JSONL line in %s", file_path)
    except OSError as e:
        logger.error("Failed to read duplicate links: %s", e)
        return [], 0
    total = len(all_rows)
    return all_rows[offset : offset + limit], total
