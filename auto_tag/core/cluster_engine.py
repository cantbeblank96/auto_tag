"""CLIP 双阈值建簇引擎：只负责特征判定与向量入库，不调用 VLM。"""
from __future__ import annotations

import logging
import math
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from auto_tag.core.duplicate_store import DuplicateLinkWriter
from auto_tag.core.path_prefix_registry import PathPrefixRegistry, resolve_stored_image_path
from auto_tag.core.pipeline_profile import PipelineProfile
from auto_tag.core.vector_db import VectorDB
from auto_tag.core.vlm_annotation_pool import AnnotationJob

logger = logging.getLogger(__name__)

_EMPTY_LABELS = "{}"


def _cosine_distance(a: List[float], b: List[float]) -> float:
    """与 Chroma cosine space 一致：L2 归一化向量上 distance = 1 - dot(a,b)。"""
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, 1.0 - dot)


@dataclass
class _BatchEntry:
    """本批已判定/入库、供后续图片检索的向量（弥补 batch 内 Chroma 不可见）。"""

    path: str
    vector: List[float]
    cluster_id: str
    labels_json: str
    is_center: bool
    doc_id: str
    meta: Dict[str, Any]


class ClusterEngine:
    """纯 CLIP 侧建簇：Stage1 侧车 / Stage2 继承 / Stage3 新簇中心先入队待标。"""

    def __init__(
        self,
        db: VectorDB,
        *,
        tau_dup: float,
        tau_cls: float,
        duplicate_link_writer: Optional[DuplicateLinkWriter] = None,
        path_prefix_registry: Optional[PathPrefixRegistry] = None,
        db_lock: Optional[threading.Lock] = None,
    ) -> None:
        self.db = db
        self.tau_dup = tau_dup
        self.tau_cls = tau_cls
        self.duplicate_link_writer = duplicate_link_writer
        self._path_registry = path_prefix_registry
        self._db_lock = db_lock or threading.Lock()

    @staticmethod
    def row_meta(
        path: str,
        cluster_id: str,
        is_center: bool,
        labels_json: str,
        decode_meta: Dict[str, Any],
        *,
        path_prefix_registry: Optional[PathPrefixRegistry] = None,
        annotation_status: str = "done",
    ) -> Dict[str, Any]:
        """构建 Chroma 行 metadata（与 ImageAutoAnnotator._row_meta 兼容）。"""
        out: Dict[str, Any] = {
            "cluster_id": cluster_id,
            "is_cluster_center": bool(is_center),
            "labels_json": labels_json,
            "annotation_status": annotation_status,
            "media_kind": str(decode_meta.get("media_kind", "rgb")),
            "pix_w": int(decode_meta.get("pix_w", 0)),
            "pix_h": int(decode_meta.get("pix_h", 0)),
            "yuv_layout": str(decode_meta.get("yuv_layout", "")),
        }
        if path_prefix_registry is not None:
            pid, rel = path_prefix_registry.split(path)
            out["path_prefix_id"] = str(pid)
            out["image_rel_path"] = rel
        else:
            out["image_path"] = path
        return out

    def generate_unique_cluster_id(self) -> str:
        """生成库内唯一的 8 位 cluster_id。"""
        while True:
            new_id = f"cls_{uuid.uuid4().hex[:8]}"
            existing = self.db.collection.get(where={"cluster_id": new_id}, limit=1)
            if not existing["ids"]:
                return new_id
            logger.warning("Collision detected for cluster_id: %s, regenerating...", new_id)

    def insert_cluster_center(
        self,
        image_path: str,
        vector: List[float],
        decode_meta: Dict[str, Any],
        *,
        profile: Optional[PipelineProfile] = None,
    ) -> Tuple[str, str, AnnotationJob]:
        """
        写入新簇中心（labels 为空、annotation_status=pending），返回 (cluster_id, doc_id, vlm_job)。
        """
        prof = profile or PipelineProfile(False)
        cluster_id = self.generate_unique_cluster_id()
        doc_id = str(uuid.uuid4())
        job = AnnotationJob(
            doc_id=doc_id,
            cluster_id=cluster_id,
            image_path=image_path,
            decode_meta=decode_meta,
        )
        with self._db_lock:
            with prof.span("chroma_add_single"):
                self.db.add_batch(
                    ids=[doc_id],
                    embeddings=[vector],
                    metadatas=[
                        self.row_meta(
                            image_path,
                            cluster_id,
                            True,
                            _EMPTY_LABELS,
                            decode_meta,
                            path_prefix_registry=self._path_registry,
                            annotation_status="pending",
                        )
                    ],
                )
        return cluster_id, doc_id, job

    def _find_batch_neighbor(
        self, vector: List[float], batch_entries: List[_BatchEntry], *, centers_only: bool
    ) -> Tuple[Optional[float], Optional[_BatchEntry]]:
        if not batch_entries:
            return None, None
        best_dist = math.inf
        best: Optional[_BatchEntry] = None
        for entry in batch_entries:
            if centers_only and not entry.is_center:
                continue
            d = _cosine_distance(vector, entry.vector)
            if d < best_dist:
                best_dist = d
                best = entry
        if best is None:
            return None, None
        return best_dist, best

    def _resolve_neighbor(
        self,
        vector: List[float],
        db_dist: Optional[float],
        db_meta: Optional[Dict[str, Any]],
        db_id: str,
        batch_entries: List[_BatchEntry],
        path: str,
    ) -> Tuple[float, Dict[str, Any], str, str]:
        """
        合并 Chroma Top-1 与本批内存索引。

        - **Stage1 近重复**：本批任意已处理图（含 Stage2 成员）距 ≤ tau_dup 即侧车。
        - **Stage2/3 聚类**：仅用 Chroma 与**本批已建簇中心**比距，避免 Stage2 成员拉低距离误并簇。
        """
        batch_dup_dist, batch_dup_entry = self._find_batch_neighbor(
            vector, batch_entries, centers_only=False
        )
        if (
            batch_dup_entry is not None
            and batch_dup_dist is not None
            and batch_dup_dist <= self.tau_dup
            and (db_dist is None or batch_dup_dist <= db_dist)
        ):
            meta = dict(batch_dup_entry.meta)
            meta.setdefault("cluster_id", batch_dup_entry.cluster_id)
            meta.setdefault("labels_json", batch_dup_entry.labels_json)
            return batch_dup_dist, meta, batch_dup_entry.doc_id, "batch_dup"

        batch_center_dist, batch_center = self._find_batch_neighbor(
            vector, batch_entries, centers_only=True
        )
        use_batch_center = (
            batch_center is not None
            and batch_center_dist is not None
            and (db_dist is None or batch_center_dist < db_dist)
        )
        if use_batch_center and batch_center is not None and batch_center_dist is not None:
            meta = dict(batch_center.meta)
            meta.setdefault("cluster_id", batch_center.cluster_id)
            meta.setdefault("labels_json", batch_center.labels_json)
            meta.setdefault("is_cluster_center", True)
            return batch_center_dist, meta, batch_center.doc_id, "batch_center"
        if db_dist is not None and db_meta is not None:
            return db_dist, db_meta, db_id, "db"
        if batch_center is not None and batch_center_dist is not None:
            meta = dict(batch_center.meta)
            meta.setdefault("cluster_id", batch_center.cluster_id)
            meta.setdefault("labels_json", batch_center.labels_json)
            return batch_center_dist, meta, batch_center.doc_id, "batch_center"
        return float("inf"), {}, "", "none"

    def process_batch(
        self,
        valid_paths: List[str],
        embeddings: List[List[float]],
        *,
        decode_metas: Optional[List[Dict[str, Any]]] = None,
        profile: Optional[PipelineProfile] = None,
        on_item_done: Optional[Callable[[Dict[str, int]], None]] = None,
        on_vlm_job: Optional[Callable[[AnnotationJob], None]] = None,
    ) -> Tuple[Dict[str, int], List[AnnotationJob]]:
        """
        执行双阈值建簇；Stage3/空库首簇立即入库并产出 VLM 任务，由调用方 submit 到标注池。

        Returns:
            (stats, vlm_jobs) — stats 中 vlm_calls 恒为 0（VLM 由池异步计数）。
        """
        zero = {"vlm_calls": 0, "stage1_skips": 0, "stage2_joins": 0, "new_centers": 0}

        def _emit(delta: Dict[str, int]) -> None:
            if on_item_done:
                on_item_done(delta)

        if not valid_paths or not embeddings:
            return dict(zero), []

        if decode_metas is None or len(decode_metas) != len(valid_paths):
            decode_metas = [
                {"media_kind": "rgb", "pix_w": 0, "pix_h": 0, "yuv_layout": ""}
                for _ in valid_paths
            ]

        stats = dict(zero)
        prof = profile or PipelineProfile(False)
        vlm_jobs: List[AnnotationJob] = []
        batch_entries: List[_BatchEntry] = []

        paths = list(valid_paths)
        embs = list(embeddings)
        metas = list(decode_metas)

        # 空库：首图立刻建簇（不阻塞 VLM），其余图再批量检索
        if self.db.count() == 0 and paths:
            first_path, first_emb, first_meta = paths[0], embs[0], metas[0]
            logger.info("Database empty. Bootstrap cluster center for %s (VLM async)", first_path)
            cluster_id, doc_id, job = self.insert_cluster_center(
                first_path, first_emb, first_meta, profile=prof
            )
            center_meta = self.row_meta(
                first_path,
                cluster_id,
                True,
                _EMPTY_LABELS,
                first_meta,
                path_prefix_registry=self._path_registry,
                annotation_status="pending",
            )
            batch_entries.append(
                _BatchEntry(
                    path=first_path,
                    vector=first_emb,
                    cluster_id=cluster_id,
                    labels_json=_EMPTY_LABELS,
                    is_center=True,
                    doc_id=doc_id,
                    meta=center_meta,
                )
            )
            vlm_jobs.append(job)
            if on_vlm_job:
                on_vlm_job(job)
            stats["new_centers"] += 1
            _emit({"vlm_calls": 0, "stage1_skips": 0, "stage2_joins": 0, "new_centers": 1})

            paths = paths[1:]
            embs = embs[1:]
            metas = metas[1:]
            if not paths:
                return stats, vlm_jobs

        with prof.span("chroma_query_batch"):
            distances, neighbor_metas, neighbor_ids = self.db.query_batch(embs, n_results=1)

        ids_to_add: List[str] = []
        embs_to_add: List[List[float]] = []
        metas_to_add: List[Dict[str, Any]] = []

        for i, path in enumerate(paths):
            vector = embs[i]
            item = {"vlm_calls": 0, "stage1_skips": 0, "stage2_joins": 0, "new_centers": 0}

            db_dist: Optional[float] = None
            db_meta: Optional[Dict[str, Any]] = None
            db_id = ""
            if distances and i < len(distances) and distances[i]:
                db_dist = distances[i][0]
                db_meta = neighbor_metas[i][0]
                if neighbor_ids and i < len(neighbor_ids) and neighbor_ids[i]:
                    db_id = neighbor_ids[i][0] or ""

            min_distance, nearest_meta, anchor_id, neighbor_src = self._resolve_neighbor(
                vector, db_dist, db_meta, db_id, batch_entries, path
            )

            if min_distance == float("inf") or not nearest_meta:
                logger.warning("No neighbors for %s; treating as new cluster.", path)
                cluster_id, doc_id, job = self.insert_cluster_center(
                    path, vector, metas[i], profile=prof
                )
                center_meta = self.row_meta(
                    path,
                    cluster_id,
                    True,
                    _EMPTY_LABELS,
                    metas[i],
                    path_prefix_registry=self._path_registry,
                    annotation_status="pending",
                )
                batch_entries.append(
                    _BatchEntry(
                        path=path,
                        vector=vector,
                        cluster_id=cluster_id,
                        labels_json=_EMPTY_LABELS,
                        is_center=True,
                        doc_id=doc_id,
                        meta=center_meta,
                    )
                )
                vlm_jobs.append(job)
                if on_vlm_job:
                    on_vlm_job(job)
                stats["new_centers"] += 1
                _emit({"vlm_calls": 0, "stage1_skips": 0, "stage2_joins": 0, "new_centers": 1})
                continue

            if min_distance <= self.tau_dup:
                logger.info(
                    "[%s] Stage 1: duplicate skip (dist=%.3f, via=%s)",
                    path,
                    min_distance,
                    neighbor_src,
                )
                stats["stage1_skips"] += 1
                item["stage1_skips"] = 1
                if self.duplicate_link_writer and anchor_id:
                    anchor_path = ""
                    if nearest_meta:
                        if self._path_registry is not None:
                            anchor_path = resolve_stored_image_path(
                                nearest_meta, self._path_registry
                            )
                        else:
                            anchor_path = str(nearest_meta.get("image_path", "") or "")
                    if not anchor_path and neighbor_src.startswith("batch"):
                        for be in batch_entries:
                            if be.doc_id == anchor_id:
                                anchor_path = be.path
                                break
                    self.duplicate_link_writer.append(
                        anchor_id, anchor_path, path, min_distance
                    )
                _emit(item)

            elif min_distance <= self.tau_cls:
                cluster_id = nearest_meta.get("cluster_id") or self.generate_unique_cluster_id()
                labels = str(nearest_meta.get("labels_json") or _EMPTY_LABELS)
                logger.info(
                    "[%s] Stage 2: join cluster '%s' (dist=%.3f, via=%s)",
                    path,
                    cluster_id,
                    min_distance,
                    neighbor_src,
                )
                stats["stage2_joins"] += 1
                item["stage2_joins"] = 1
                doc_id = str(uuid.uuid4())
                row = self.row_meta(
                    path,
                    cluster_id,
                    False,
                    labels,
                    metas[i],
                    path_prefix_registry=self._path_registry,
                    annotation_status=(
                        str(nearest_meta.get("annotation_status") or "done")
                        if labels != _EMPTY_LABELS
                        else "pending"
                    ),
                )
                ids_to_add.append(doc_id)
                embs_to_add.append(vector)
                metas_to_add.append(row)
                batch_entries.append(
                    _BatchEntry(
                        path=path,
                        vector=vector,
                        cluster_id=cluster_id,
                        labels_json=labels,
                        is_center=False,
                        doc_id=doc_id,
                        meta=row,
                    )
                )
                _emit(item)

            else:
                logger.info(
                    "[%s] Stage 3: new cluster (dist=%.3f, VLM async)", path, min_distance
                )
                cluster_id, doc_id, job = self.insert_cluster_center(
                    path, vector, metas[i], profile=prof
                )
                center_meta = self.row_meta(
                    path,
                    cluster_id,
                    True,
                    _EMPTY_LABELS,
                    metas[i],
                    path_prefix_registry=self._path_registry,
                    annotation_status="pending",
                )
                batch_entries.append(
                    _BatchEntry(
                        path=path,
                        vector=vector,
                        cluster_id=cluster_id,
                        labels_json=_EMPTY_LABELS,
                        is_center=True,
                        doc_id=doc_id,
                        meta=center_meta,
                    )
                )
                vlm_jobs.append(job)
                if on_vlm_job:
                    on_vlm_job(job)
                stats["new_centers"] += 1
                _emit({"vlm_calls": 0, "stage1_skips": 0, "stage2_joins": 0, "new_centers": 1})

        if ids_to_add:
            with self._db_lock:
                with prof.span("chroma_add_batch"):
                    self.db.add_batch(ids_to_add, embs_to_add, metas_to_add)

        return stats, vlm_jobs
