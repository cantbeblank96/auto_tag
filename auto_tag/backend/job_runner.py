"""单任务后台执行 pipeline，内存中保存状态与日志尾部。"""
from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import replace
from typing import Any, Callable, Deque, Dict, List, Optional, TypeVar

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
            "stage1_skips": 0,
            "stage2_joins": 0,
        }
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
            mem_handler = _memory_handler(logs)
            root.addHandler(mem_handler)

            def on_progress(
                done: int,
                tot: int,
                failed_n: int,
                *,
                skip_in_db: int = 0,
                vlm_calls: int = 0,
                stage1_skips: int = 0,
                stage2_joins: int = 0,
            ) -> None:
                _jobs[job_id]["processed"] = done
                _jobs[job_id]["total"] = tot
                _jobs[job_id]["failed_so_far"] = failed_n
                _jobs[job_id]["skip_in_db"] = skip_in_db
                _jobs[job_id]["vlm_calls"] = vlm_calls
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
            try:
                write_build_snapshot(work_log_dir(cfg.work_dir), cfg)
            except Exception:
                logger.exception("write_build_snapshot failed for job %s", job_id)
        except Exception as e:
            logger.exception("Job %s failed", job_id)
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(e)
        finally:
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
