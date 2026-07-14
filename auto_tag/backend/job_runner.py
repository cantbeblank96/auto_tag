"""单任务后台执行 pipeline，内存中保存状态与日志尾部。"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import replace
from typing import Any, Callable, Deque, Dict, List, Optional, TypeVar

from auto_tag.backend.job_store import hydrate_jobs_from_disk, persist_job_record
from auto_tag.core.db_build_snapshot import write_build_snapshot
from auto_tag.core.pipeline import (
    PipelineConfig,
    collect_image_paths,
    normalize_work_dir,
    run_annotation_pipeline,
    work_log_dir,
)

logger = logging.getLogger(__name__)

_submit_lock = threading.Lock()
_busy = False
_jobs: Dict[str, Dict[str, Any]] = {}
_server_started_at: float = time.time()

# 从磁盘恢复历史任务（后端重启后仍可查询）
try:
    hydrate_jobs_from_disk(_jobs)
except Exception:
    logger.exception("hydrate_jobs_from_disk failed")


def _memory_handler(logs: Deque[str]) -> logging.Handler:
    class H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                logs.append(self.format(record))
            except Exception:
                pass

    h = H()
    h.setLevel(logging.INFO)
    h.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    )
    return h


def submit_job(cfg: PipelineConfig) -> str:
    """若已有任务在运行，抛出 RuntimeError。"""
    global _busy
    cfg = replace(cfg, work_dir=normalize_work_dir(cfg.work_dir))

    with _submit_lock:
        if _busy:
            raise RuntimeError("Another job is already running.")
        _busy = True

    try:
        job_id = str(uuid.uuid4())
        image_list, _ = collect_image_paths(cfg.input_dirs, cfg.image_ls_files)
        logs: Deque[str] = deque(maxlen=8000)
        _jobs[job_id] = {
            "status": "queued",
            "error": None,
            "processed": 0,
            "total": len(image_list),
            "work_dir": cfg.work_dir,
            "log_dir": work_log_dir(cfg.work_dir),
            "logs": logs,
            "failed_count": None,
            "failed_so_far": 0,
            "skip_in_db": 0,
            "vlm_calls": 0,
            "new_centers": 0,
            "stage1_skips": 0,
            "stage2_joins": 0,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
        }
        persist_job_record(job_id, _jobs[job_id])
    except Exception:
        with _submit_lock:
            _busy = False
        raise

    def run() -> None:
        global _busy
        mem_handler: Optional[logging.Handler] = None
        root = logging.getLogger()
        try:
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["started_at"] = time.time()
            persist_job_record(job_id, _jobs[job_id])
            mem_handler = _memory_handler(logs)
            root.addHandler(mem_handler)

            def on_progress(
                done: int,
                tot: int,
                failed_n: int,
                *,
                skip_in_db: int = 0,
                vlm_calls: int = 0,
                new_centers: int = 0,
                stage1_skips: int = 0,
                stage2_joins: int = 0,
            ) -> None:
                _jobs[job_id]["processed"] = done
                _jobs[job_id]["total"] = tot
                _jobs[job_id]["failed_so_far"] = failed_n
                _jobs[job_id]["skip_in_db"] = skip_in_db
                _jobs[job_id]["vlm_calls"] = vlm_calls
                _jobs[job_id]["new_centers"] = new_centers
                _jobs[job_id]["stage1_skips"] = stage1_skips
                _jobs[job_id]["stage2_joins"] = stage2_joins

            result = run_annotation_pipeline(
                cfg,
                on_progress=on_progress,
            )
            n_failed = len(result.failed_paths)
            _jobs[job_id]["failed_count"] = n_failed
            _jobs[job_id]["failed_so_far"] = n_failed
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["processed"] = result.total_images
            _jobs[job_id]["finished_at"] = time.time()
            try:
                write_build_snapshot(work_log_dir(cfg.work_dir), cfg)
            except Exception:
                logger.exception("write_build_snapshot failed for job %s", job_id)
            persist_job_record(job_id, _jobs[job_id])
        except Exception as e:
            logger.exception("Job %s failed", job_id)
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(e)
            _jobs[job_id]["finished_at"] = time.time()
            persist_job_record(job_id, _jobs[job_id])
        finally:
            if _jobs.get(job_id, {}).get("finished_at") is None:
                _jobs[job_id]["finished_at"] = time.time()
                try:
                    persist_job_record(job_id, _jobs[job_id])
                except Exception:
                    logger.exception("persist job finished_at failed for %s", job_id)
            try:
                from auto_tag.core.vlm_endpoint_stats_store import persist_circuit_breaker_states

                persist_circuit_breaker_states(cfg.work_dir)
            except Exception:
                logger.exception("persist VLM endpoint stats failed for job %s", job_id)
            if mem_handler is not None:
                root.removeHandler(mem_handler)
            with _submit_lock:
                _busy = False

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _jobs.get(job_id)


def get_job_logs(job_id: str, tail: int = 200) -> List[str]:
    j = _jobs.get(job_id)
    if not j:
        return []
    logs: Deque[str] = j["logs"]
    if tail <= 0:
        return list(logs)
    return list(logs)[-tail:]


def list_jobs() -> List[Dict[str, Any]]:
    """返回所有历史任务摘要（不含 logs 以减少传输量）。"""
    out: List[Dict[str, Any]] = []
    for jid, j in _jobs.items():
        out.append({
            "job_id": jid,
            "status": j["status"],
            "processed": j["processed"],
            "total": j["total"],
            "error": j["error"],
            "failed_count": j["failed_count"],
            "failed_so_far": j.get("failed_so_far", 0),
            "skip_in_db": j.get("skip_in_db", 0),
            "vlm_calls": j.get("vlm_calls", 0),
            "new_centers": j.get("new_centers", 0),
            "stage1_skips": j.get("stage1_skips", 0),
            "stage2_joins": j.get("stage2_joins", 0),
            "work_dir": j.get("work_dir", ""),
            "log_dir": j.get("log_dir", ""),
            "created_at": j.get("created_at", 0),
            "started_at": j.get("started_at"),
            "finished_at": j.get("finished_at"),
        })
    out.sort(key=lambda x: x.get("created_at", 0))
    return out


def get_server_started_at() -> float:
    return _server_started_at


def is_busy() -> bool:
    with _submit_lock:
        return _busy


T = TypeVar("T")


def run_exclusive_task(fn: Callable[[], T]) -> T:
    """与 submit_job 互斥：用于数据库维护等长时间操作。"""
    global _busy
    with _submit_lock:
        if _busy:
            raise RuntimeError("已有任务在运行（标注或维护），请稍后再试。")
        _busy = True
    try:
        return fn()
    finally:
        with _submit_lock:
            _busy = False
