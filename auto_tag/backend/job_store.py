"""Web 标注任务历史持久化（work_dir/log/web_job_history.json）。"""
from __future__ import annotations

import logging
import os
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from kevin_toolbox.data_flow.file import json_

from auto_tag.core.config import settings
from auto_tag.core.pipeline import normalize_work_dir, work_log_dir

logger = logging.getLogger(__name__)

_HISTORY_FILENAME = "web_job_history.json"
_lock = threading.Lock()


def _default_work_root() -> str:
    wd = settings.work_dir
    if wd and str(wd).strip():
        return normalize_work_dir(str(wd))
    emb = os.path.realpath(
        os.path.abspath(os.path.expanduser(str(settings.db_path).strip()))
    )
    return os.path.dirname(emb.rstrip(os.sep)) or os.getcwd()


def history_file_path() -> str:
    return os.path.join(work_log_dir(_default_work_root()), _HISTORY_FILENAME)


def _record_from_job(job_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "error": job.get("error"),
        "processed": int(job.get("processed") or 0),
        "total": int(job.get("total") or 0),
        "work_dir": str(job.get("work_dir") or ""),
        "log_dir": str(job.get("log_dir") or ""),
        "failed_count": job.get("failed_count"),
        "failed_so_far": int(job.get("failed_so_far") or 0),
        "skip_in_db": int(job.get("skip_in_db") or 0),
        "vlm_calls": int(job.get("vlm_calls") or 0),
        "stage1_skips": int(job.get("stage1_skips") or 0),
        "stage2_joins": int(job.get("stage2_joins") or 0),
        "created_at": float(job.get("created_at") or 0),
    }


def _load_all_records() -> Dict[str, Dict[str, Any]]:
    path = history_file_path()
    if not os.path.isfile(path):
        return {}
    try:
        raw = json_.read(file_path=path, b_use_suggested_converter=True)
    except Exception:
        logger.exception("read job history failed: %s", path)
        return {}
    if isinstance(raw, list):
        out: Dict[str, Dict[str, Any]] = {}
        for item in raw:
            if isinstance(item, dict) and item.get("job_id"):
                out[str(item["job_id"])] = item
        return out
    if isinstance(raw, dict):
        jobs = raw.get("jobs")
        if isinstance(jobs, list):
            out: Dict[str, Dict[str, Any]] = {}
            for item in jobs:
                if isinstance(item, dict) and item.get("job_id"):
                    out[str(item["job_id"])] = item
            return out
        if isinstance(jobs, dict):
            return {str(k): v for k, v in jobs.items() if isinstance(v, dict)}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict) and v.get("job_id")}
    return {}


def _save_all_records(records: Dict[str, Dict[str, Any]]) -> None:
    path = history_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ordered: List[Dict[str, Any]] = sorted(
        records.values(),
        key=lambda x: float(x.get("created_at") or 0),
    )
    json_.write(
        content={"jobs": ordered},
        file_path=path,
        b_use_suggested_converter=True,
    )


def persist_job_record(job_id: str, job: Dict[str, Any]) -> None:
    """将任务摘要写入磁盘（不含 logs）。"""
    rec = _record_from_job(job_id, job)
    with _lock:
        all_records = _load_all_records()
        all_records[job_id] = rec
        _save_all_records(all_records)


def hydrate_jobs_from_disk(target: Dict[str, Dict[str, Any]]) -> int:
    """启动时从磁盘恢复任务；running/queued 标为 failed（中断）。"""
    loaded = 0
    for job_id, rec in _load_all_records().items():
        if job_id in target:
            continue
        status = str(rec.get("status") or "")
        error = rec.get("error")
        if status in ("running", "queued"):
            status = "failed"
            error = "后端重启导致任务中断"
        logs: Deque[str] = deque(maxlen=8000)
        target[job_id] = {
            "status": status,
            "error": error,
            "processed": int(rec.get("processed") or 0),
            "total": int(rec.get("total") or 0),
            "work_dir": rec.get("work_dir") or "",
            "log_dir": rec.get("log_dir") or "",
            "logs": logs,
            "failed_count": rec.get("failed_count"),
            "failed_so_far": int(rec.get("failed_so_far") or 0),
            "skip_in_db": int(rec.get("skip_in_db") or 0),
            "vlm_calls": int(rec.get("vlm_calls") or 0),
            "stage1_skips": int(rec.get("stage1_skips") or 0),
            "stage2_joins": int(rec.get("stage2_joins") or 0),
            "created_at": float(rec.get("created_at") or 0),
        }
        loaded += 1
    return loaded
