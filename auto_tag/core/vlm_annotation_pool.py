"""异步 VLM 标注池：CLIP 建簇完成后将簇中心入队，worker 并行打标并回写索引。"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from auto_tag.core.config import settings
from auto_tag.core.image_load_context import ImageLoadContext
from auto_tag.core.pipeline_profile import PipelineProfile
from auto_tag.core.vector_db import VectorDB
from auto_tag.core.vlm_client import VLMClient
from auto_tag.core.vlm_timing_collector import record as timing_record

logger = logging.getLogger(__name__)

_job_ctx = threading.local()


def _set_job_ctx(cluster_id: str, path: str) -> None:
    _job_ctx.cluster_id = cluster_id
    _job_ctx.path = path


def _clear_job_ctx() -> None:
    _job_ctx.cluster_id = ""
    _job_ctx.path = ""


def _job_label() -> str:
    p = getattr(_job_ctx, "path", "") or ""
    return os.path.basename(p) if p else ""


def _job_cluster() -> str:
    return getattr(_job_ctx, "cluster_id", "") or ""

_SENTINEL = object()


@dataclass(frozen=True)
class AnnotationJob:
    """待 VLM 标注的簇中心任务。"""

    doc_id: str
    cluster_id: str
    image_path: str
    decode_meta: Dict[str, Any]


class VlmAnnotationPool:
    """
    全局 VLM 消费者池：与 CLIP 建簇流水线并行运行。

    - submit：非阻塞入队
    - wait_idle：等待队列与在途任务清空
    - shutdown：停止 worker（可选丢弃未开始任务）
    """

    def __init__(
        self,
        *,
        vlm: VLMClient,
        db: VectorDB,
        db_lock: threading.Lock,
        load_context: ImageLoadContext,
        profile: Optional[PipelineProfile] = None,
        on_vlm_done: Optional[Callable[[], None]] = None,
    ) -> None:
        self.vlm = vlm
        self.db = db
        self._db_lock = db_lock
        self.load_context = load_context
        self.profile = profile or PipelineProfile(False)
        self.on_vlm_done = on_vlm_done

        self._queue: queue.Queue[Any] = queue.Queue()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._in_flight = 0
        self._in_flight_lock = threading.Lock()
        self._stopped = False
        self._workers = max(1, int(getattr(settings, "vlm_concurrency", 1) or 1))

    def start(self) -> None:
        """启动 worker 线程池。"""
        if self._executor is not None:
            return
        self._stopped = False
        n = self._workers
        self._executor = ThreadPoolExecutor(max_workers=n, thread_name_prefix="vlm-pool")
        for _ in range(n):
            self._executor.submit(self._worker_loop)
        logger.info("VLM annotation pool started with %d workers", n)
        timing_record("vlm_pool_start", workers=n)

    def submit(self, job: AnnotationJob) -> None:
        """将簇中心标注任务入队（非阻塞）。"""
        if self._stopped:
            logger.warning("VLM pool stopped; dropping job for %s", job.image_path)
            return
        timing_record(
            "job_enqueued",
            cluster_id=job.cluster_id,
            path=os.path.basename(job.image_path),
            queue_size=self._queue.qsize(),
        )
        self._queue.put(job)

    def _inc_in_flight(self) -> None:
        with self._in_flight_lock:
            self._in_flight += 1

    def _dec_in_flight(self) -> None:
        with self._in_flight_lock:
            self._in_flight -= 1

    def _in_flight_count(self) -> int:
        with self._in_flight_lock:
            return self._in_flight

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        """等待队列与在途任务全部完成。返回是否在超时前完成。"""
        if timeout is None:
            self._queue.join()
            while self._in_flight_count() > 0:
                time.sleep(0.05)
            return True

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._queue.unfinished_tasks == 0 and self._in_flight_count() == 0:
                return True
            time.sleep(0.05)
        return self._queue.unfinished_tasks == 0 and self._in_flight_count() == 0

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        """关闭 worker 池。"""
        self._stopped = True
        if cancel_pending:
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
        n = self._workers
        for _ in range(n):
            self._queue.put(_SENTINEL)
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                job: AnnotationJob = item
                self._inc_in_flight()
                try:
                    self._process_job(job)
                finally:
                    self._dec_in_flight()
            finally:
                self._queue.task_done()

    def _process_job(self, job: AnnotationJob) -> None:
        prof = self.profile
        labels_dict: Dict[str, Any] = {}
        thread_name = threading.current_thread().name
        _set_job_ctx(job.cluster_id, job.image_path)
        timing_record(
            "job_worker_start",
            cluster_id=job.cluster_id,
            path=os.path.basename(job.image_path),
            thread=thread_name,
        )
        try:
            try:
                load_t0 = time.perf_counter()
                img = self.load_context.load_path(job.image_path, job.decode_meta)
                timing_record(
                    "job_load_done",
                    cluster_id=job.cluster_id,
                    path=os.path.basename(job.image_path),
                    load_s=round(time.perf_counter() - load_t0, 3),
                )
            except Exception as e:
                logger.error("VLM worker failed to load %s: %s", job.image_path, e)
                self._mark_center_failed(job)
                timing_record("job_failed", cluster_id=job.cluster_id, phase="load")
                return

            try:
                timing_record(
                    "job_vlm_start",
                    cluster_id=job.cluster_id,
                    path=os.path.basename(job.image_path),
                    thread=thread_name,
                )
                vlm_t0 = time.perf_counter()
                with prof.span_wall("vlm_annotate"):
                    with prof.span("vlm_annotate_thread"):
                        labels_dict = self.vlm.annotate_image(img, profile=prof)
                timing_record(
                    "job_vlm_done",
                    cluster_id=job.cluster_id,
                    path=os.path.basename(job.image_path),
                    vlm_s=round(time.perf_counter() - vlm_t0, 3),
                )
                if not isinstance(labels_dict, dict):
                    labels_dict = {}
                v = VLMClient.validate_against_questions(labels_dict)
                if not v["valid"]:
                    logger.warning(
                        "VLM result still invalid after correction for %s: %s",
                        job.image_path,
                        "; ".join(v["errors"]),
                    )
            except Exception as e:
                logger.error("VLM annotate failed for %s: %s", job.image_path, e)
                self._mark_center_failed(job)
                timing_record("job_failed", cluster_id=job.cluster_id, phase="vlm")
                return

            labels_json = json.dumps(labels_dict, ensure_ascii=False)
            apply_t0 = time.perf_counter()
            self._apply_labels(job, labels_json)
            timing_record(
                "job_done",
                cluster_id=job.cluster_id,
                path=os.path.basename(job.image_path),
                apply_s=round(time.perf_counter() - apply_t0, 3),
                thread=thread_name,
            )
            if self.on_vlm_done:
                try:
                    self.on_vlm_done()
                except Exception as e:
                    logger.warning("on_vlm_done callback error: %s", e)
        finally:
            _clear_job_ctx()

    def _mark_center_failed(self, job: AnnotationJob) -> None:
        with self._db_lock:
            try:
                r = self.db.collection.get(ids=[job.doc_id], include=["metadatas"])
                metas = r.get("metadatas") or []
                if not metas or not metas[0]:
                    return
                meta = dict(metas[0])
                meta["annotation_status"] = "failed"
                self.db.update_document_metadata(job.doc_id, meta)
            except Exception as e:
                logger.error("Failed to mark annotation failed for %s: %s", job.doc_id, e)

    def _apply_labels(self, job: AnnotationJob, labels_json: str) -> None:
        """更新簇中心 labels，并将标签传播到同簇非中心成员。"""
        with self._db_lock:
            try:
                r = self.db.collection.get(ids=[job.doc_id], include=["metadatas"])
                metas = r.get("metadatas") or []
                if not metas or not metas[0]:
                    logger.warning("Center doc %s not found for label update", job.doc_id)
                    return
                center_meta = dict(metas[0])
                center_meta["labels_json"] = labels_json
                center_meta["annotation_status"] = "done"
                self.db.update_document_metadata(job.doc_id, center_meta)

                member_ids, member_metas = self.db.get_cluster_members(job.cluster_id)
                for mid, mmeta in zip(member_ids, member_metas):
                    if mid == job.doc_id:
                        continue
                    if not mmeta:
                        continue
                    if mmeta.get("is_cluster_center"):
                        continue
                    new_m = dict(mmeta)
                    new_m["labels_json"] = labels_json
                    new_m["annotation_status"] = "done"
                    self.db.update_document_metadata(mid, new_m)
            except Exception as e:
                logger.error(
                    "Failed to apply labels for cluster %s (%s): %s",
                    job.cluster_id,
                    job.image_path,
                    e,
                )
