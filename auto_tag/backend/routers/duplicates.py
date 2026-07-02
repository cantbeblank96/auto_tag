import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

from auto_tag.backend.public_json import public_duplicate_row
from auto_tag.core.config import settings
from auto_tag.core.duplicate_store import read_duplicate_store
from auto_tag.core.pipeline import work_log_dir

router = APIRouter(prefix="/duplicates", tags=["duplicates"])


def _dedupe_duplicate_rows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一 dup_path + anchor_path 保留 ts 最新的一条，避免多次任务重复追加导致列表重复。"""
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in items:
        dp = str(row.get("dup_path") or "")
        ap = str(row.get("anchor_path") or "")
        key = (dp, ap)
        cur = best.get(key)
        ts = str(row.get("ts") or "")
        if cur is None or ts >= str(cur.get("ts") or ""):
            best[key] = row
    out = list(best.values())
    out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return out


@router.get("")
def list_duplicates(
    work_dir: Optional[str] = Query(
        None, description="工作根目录，读取 {work_dir}/log 下 duplicate 存储（默认 SQLite）"
    ),
    output_dir: Optional[str] = Query(
        None,
        description="已废弃，等同于 work_dir",
    ),
    log_dir: Optional[str] = Query(
        None,
        description="兼容：直接指向 log 目录（内含 duplicate_links.sqlite 或 .jsonl）",
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> Dict[str, Any]:
    root = (work_dir or output_dir or "").strip()
    if root:
        log_dir_path = work_log_dir(root)
    elif log_dir:
        log_dir_path = os.path.abspath(os.path.expanduser(log_dir))
    else:
        # 未指定时从 settings.db_path 反向推导
        emb = os.path.realpath(
            os.path.abspath(os.path.expanduser(str(settings.db_path).strip()))
        )
        log_dir_path = os.path.join(os.path.dirname(emb.rstrip(os.sep)), "log") or os.path.join(".", "log")

    if not os.path.isdir(log_dir_path):
        raise HTTPException(status_code=400, detail="Resolved log directory does not exist")
    file_path = os.path.join(log_dir_path, settings.duplicate_links_filename)
    raw_items, total = read_duplicate_store(
        file_path, limit=limit, offset=offset, log_dir=log_dir_path
    )
    items = [public_duplicate_row(r) for r in _dedupe_duplicate_rows(raw_items)]
    return {
        "work_dir": root or None,
        "log_dir": log_dir_path,
        "file": file_path,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }
