"""单任务后台执行 pipeline，内存中保存状态与日志尾部。"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, fields, replace
from typing import Any, Callable, Deque, Dict, List, Optional, TypeVar

from auto_tag.backend.job_store import (
    delete_job_records,
    hydrate_jobs_from_disk,
    persist_job_record,
)
from auto_tag.core.db_build_snapshot import write_build_snapshot
from auto_tag.core.pipeline import (
    PipelineConfig,
    build_image_filter_spec,
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


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"


def job_log_file(job_id: str) -> str:
    """任务级日志落盘路径：work_dir/log/jobs/job_{job_id}.log（后端重启不丢）。"""
    j = _jobs.get(job_id)
    if not j:
        return ""
    return os.path.join(j.get("log_dir") or "", "jobs", f"job_{job_id}.log")


def job_failed_file(job_id: str) -> str:
    """任务失败图片列表落盘路径：work_dir/log/jobs/job_{job_id}_failed.json。

    含两类失败：加载/批处理失败的图片 + 簇中心 VLM 标注失败的图片。
    """
    j = _jobs.get(job_id)
    if not j:
        return ""
    return os.path.join(j.get("log_dir") or "", "jobs", f"job_{job_id}_failed.json")


def read_failed_images(job_id: str) -> List[str]:
    """读取任务的失败图片列表（文件不存在时返回空列表）。"""
    path = job_failed_file(job_id)
    if not path or not os.path.isfile(path):
        return []
    try:
        import json as _json

        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except Exception:
        logger.exception("read failed images list failed for %s", job_id)
    return []


def _memory_handler(logs: Deque[str]) -> logging.Handler:
    class H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                logs.append(self.format(record))
            except Exception:
                pass

    h = H()
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter(_LOG_FORMAT))
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
        image_list, _ = collect_image_paths(
            cfg.input_dirs,
            cfg.image_ls_files,
            filter_spec=build_image_filter_spec(
                image_suffixes=cfg.image_suffixes,
                image_name_regex=cfg.image_name_regex,
                filter_ignore_case=cfg.filter_ignore_case,
                filter_match_full_path=cfg.filter_match_full_path,
            ),
        )
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
            "vlm_failed": 0,
            "new_centers": 0,
            "stage1_skips": 0,
            "stage2_joins": 0,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            # 保留提交时的配置快照，供“重跑失败部分”重建任务
            "cfg_dict": asdict(cfg),
        }
        persist_job_record(job_id, _jobs[job_id])
    except Exception:
        with _submit_lock:
            _busy = False
        raise

    def run() -> None:
        global _busy
        mem_handler: Optional[logging.Handler] = None
        file_handler: Optional[logging.FileHandler] = None
        root = logging.getLogger()
        # 任务期间将 root logger 提升到 INFO，使任务级 handler 能收到建簇/阶段等 INFO 日志；结束后恢复
        prev_root_level = root.level
        root.setLevel(logging.INFO)
        try:
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["started_at"] = time.time()
            persist_job_record(job_id, _jobs[job_id])
            mem_handler = _memory_handler(logs)
            root.addHandler(mem_handler)
            # 任务日志同步落盘，供重启后追溯与前端下载
            log_path = job_log_file(job_id)
            if log_path:
                try:
                    os.makedirs(os.path.dirname(log_path), exist_ok=True)
                    file_handler = logging.FileHandler(log_path, encoding="utf-8")
                    file_handler.setLevel(logging.INFO)
                    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
                    root.addHandler(file_handler)
                except Exception:
                    logger.exception("create job log file failed for %s", job_id)
                    file_handler = None

            def on_progress(
                done: int,
                tot: int,
                failed_n: int,
                *,
                skip_in_db: int = 0,
                vlm_calls: int = 0,
                vlm_failed: int = 0,
                new_centers: int = 0,
                stage1_skips: int = 0,
                stage2_joins: int = 0,
            ) -> None:
                _jobs[job_id]["processed"] = done
                _jobs[job_id]["total"] = tot
                _jobs[job_id]["failed_so_far"] = failed_n
                _jobs[job_id]["skip_in_db"] = skip_in_db
                _jobs[job_id]["vlm_calls"] = vlm_calls
                _jobs[job_id]["vlm_failed"] = vlm_failed
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
            # 失败图片列表落盘（含 VLM 标注失败的簇中心），供“重跑失败部分”
            all_failed: List[str] = list(result.failed_paths)
            for p in result.vlm_failed_paths:
                if p not in all_failed:
                    all_failed.append(p)
            if all_failed:
                try:
                    failed_path = job_failed_file(job_id)
                    os.makedirs(os.path.dirname(failed_path), exist_ok=True)
                    import json as _json

                    with open(failed_path, "w", encoding="utf-8") as f:
                        _json.dump(all_failed, f, ensure_ascii=False, indent=2)
                except Exception:
                    logger.exception("write failed images list failed for %s", job_id)
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
            if file_handler is not None:
                root.removeHandler(file_handler)
                try:
                    file_handler.close()
                except Exception:
                    pass
            root.setLevel(prev_root_level)
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
    if logs:
        if tail <= 0:
            return list(logs)
        return list(logs)[-tail:]
    # 内存日志为空（后端重启后）时回退读落盘日志
    path = job_log_file(job_id)
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = [line.rstrip("\n") for line in f]
        except Exception:
            return []
        if tail <= 0:
            return lines
        return lines[-tail:]
    return []


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
            "vlm_failed": j.get("vlm_failed", 0),
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


def rerun_failed_job(job_id: str) -> str:
    """单独重跑某任务的失败图片，返回新任务 ID。

    以原任务配置快照重建 PipelineConfig，将失败列表作为 image_ls 输入；
    强制 skip_if_in_db=False，确保已在索引中的失败簇中心也会被重新处理。
    """
    j = _jobs.get(job_id)
    if not j:
        raise KeyError("Job not found")
    failed_paths = read_failed_images(job_id)
    if not failed_paths:
        raise FileNotFoundError("该任务无失败图片记录（或历史任务未落盘失败列表）")
    cfg_dict = j.get("cfg_dict") or {}
    if not cfg_dict:
        raise ValueError("该任务未保存配置快照（旧版本提交的任务），无法重跑")
    valid_fields = {f.name for f in fields(PipelineConfig)}
    cfg = PipelineConfig(
        **{k: v for k, v in cfg_dict.items() if k in valid_fields}
    )
    # 仅跑失败列表：清空目录扫描，失败文件本身兼容旧格式 JSON 数组
    failed_file = job_failed_file(job_id)
    cfg = replace(
        cfg,
        input_dirs=[],
        image_ls_files=[failed_file],
        skip_if_in_db=False,
    )
    logger.info(
        "Rerunning %d failed images from job %s", len(failed_paths), job_id
    )
    return submit_job(cfg)


def delete_jobs(job_ids: List[str]) -> Dict[str, Any]:
    """删除任务记录（内存 + 磁盘历史）；运行中/排队中的任务拒绝删除。"""
    deleted: List[str] = []
    rejected: List[str] = []
    missing: List[str] = []
    for job_id in job_ids:
        j = _jobs.get(job_id)
        if j is None:
            missing.append(job_id)
            continue
        if j.get("status") in ("running", "queued"):
            rejected.append(job_id)
            continue
        del _jobs[job_id]
        deleted.append(job_id)
    if deleted:
        try:
            delete_job_records(deleted)
        except Exception:
            logger.exception("delete job records failed: %s", deleted)
    return {"deleted": deleted, "rejected": rejected, "missing": missing}
