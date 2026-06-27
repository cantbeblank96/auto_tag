import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image
from auto_tag.core.config import settings
from auto_tag.core.duplicate_store import DuplicateLinkWriter
from auto_tag.core.feature_extractor import FeatureExtractor
from auto_tag.core.path_prefix_registry import PathPrefixRegistry, resolve_stored_image_path
from auto_tag.core.vector_db import VectorDB
from auto_tag.core.vlm_client import VLMClient

logger = logging.getLogger(__name__)


class ImageAutoAnnotator:
    def __init__(
        self,
        duplicate_link_writer: Optional[DuplicateLinkWriter] = None,
        *,
        db_path: Optional[str] = None,
        path_prefix_registry: Optional[PathPrefixRegistry] = None,
    ):
        """
        初始化自动图像标注系统。
        集成特征提取、向量数据库及 VLM 标注模块。

        Args:
            duplicate_link_writer: 若提供，Stage 1 冗余帧会追加写入 JSONL。
            db_path: 覆盖向量库持久化路径；默认使用 settings.db_path。
            path_prefix_registry: 若提供，Chroma 元数据写入 path_prefix_id + image_rel_path。
        """
        logger.info("Initializing Image Auto Annotator...")
        self.extractor = FeatureExtractor(
            model_name=settings.clip_model_name,
            device=settings.device
        )
        self.db = VectorDB(
            db_path=db_path if db_path is not None else settings.db_path,
            collection_name=settings.collection_name
        )
        self.vlm = VLMClient(
            model_name=settings.vlm_model_name,
            api_key=settings.vlm_api_key
        )
        self.tau_dup = settings.tau_dup
        self.tau_cls = settings.tau_cls
        self.duplicate_link_writer = duplicate_link_writer
        self._path_registry = path_prefix_registry

    @staticmethod
    def _row_meta(
        path: str,
        cluster_id: str,
        is_center: bool,
        labels_json: str,
        decode_meta: Dict[str, Any],
        *,
        path_prefix_registry: Optional[PathPrefixRegistry] = None,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "cluster_id": cluster_id,
            "is_cluster_center": bool(is_center),
            "labels_json": labels_json,
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

    def process_batch(
        self,
        valid_paths: List[str],
        images: List[Image.Image],
        *,
        decode_metas: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, int]:
        """
        批量处理图像，执行双阈值聚类与标注核心业务流。

        Returns:
            本批统计：vlm_calls（调用大模型打标次数）、stage1_skips（近重复未入库）、
            stage2_joins（并入已有簇、未打标）。
        """
        zero = {"vlm_calls": 0, "stage1_skips": 0, "stage2_joins": 0}
        if not valid_paths or not images:
            return dict(zero)

        if decode_metas is None or len(decode_metas) != len(valid_paths):
            decode_metas = [
                {"media_kind": "rgb", "pix_w": 0, "pix_h": 0, "yuv_layout": ""}
                for _ in valid_paths
            ]

        stats = dict(zero)

        # 1. 批量特征提取
        embeddings = self.extractor.extract_features_batch(images)
        if not embeddings:
            return dict(zero)

        # 若数据库为空，直接将第一张图作为初始新簇入库
        if self.db.count() == 0:
            first_path = valid_paths[0]
            first_emb = embeddings[0]
            first_img = images[0]
            logger.info(f"Database is empty. Creating initial cluster for {first_path}")
            self._create_new_cluster(
                first_path, first_emb, first_img, decode_metas[0]
            )
            stats["vlm_calls"] += 1

            # 从剩下的开始处理
            valid_paths = valid_paths[1:]
            embeddings = embeddings[1:]
            images = images[1:]
            if not valid_paths:
                return stats

        # 2. 批量向量查询最近邻 (Top-1)
        distances, metadatas, neighbor_ids = self.db.query_batch(embeddings, n_results=1)

        # 准备批量插入的数据容器
        ids_to_add = []
        embs_to_add = []
        metas_to_add = []

        # 3. 逐个进行双阈值判定与处理
        for i, path in enumerate(valid_paths):
            vector = embeddings[i]

            # 防御性判断
            if not distances or i >= len(distances) or not distances[i]:
                logger.warning(f"No neighbors found for {path}, treating as new cluster.")
                self._create_new_cluster(path, vector, images[i], decode_metas[i])
                stats["vlm_calls"] += 1
                continue

            min_distance = distances[i][0]
            nearest_meta = metadatas[i][0]
            anchor_id = ""
            if neighbor_ids and i < len(neighbor_ids) and neighbor_ids[i]:
                anchor_id = neighbor_ids[i][0] or ""

            if min_distance <= self.tau_dup:
                logger.info(f"[{path}] Stage 1: Duplicate/Redundant. Skipping insert. (Dist: {min_distance:.3f})")
                stats["stage1_skips"] += 1
                if self.duplicate_link_writer and anchor_id:
                    anchor_path = ""
                    if nearest_meta:
                        if self._path_registry is not None:
                            anchor_path = resolve_stored_image_path(
                                nearest_meta, self._path_registry
                            )
                        else:
                            anchor_path = str(nearest_meta.get("image_path", "") or "")
                    self.duplicate_link_writer.append(
                        anchor_id, anchor_path, path, min_distance
                    )

            elif min_distance <= self.tau_cls:
                # [阶段 2] 继承已有簇 ID。如果元数据中缺失，则回退生成一个新的唯一 ID (带查重)
                cluster_id = nearest_meta.get('cluster_id')
                if not cluster_id:
                    cluster_id = self._generate_unique_cluster_id()

                labels = str(nearest_meta.get("labels_json") or "{}")
                logger.info(
                    f"[{path}] Stage 2: Same Cluster '{cluster_id}'. Inheriting labels. (Dist: {min_distance:.3f})")

                stats["stage2_joins"] += 1
                ids_to_add.append(str(uuid.uuid4()))
                embs_to_add.append(vector)
                metas_to_add.append(
                    self._row_meta(
                        path,
                        cluster_id,
                        False,
                        labels,
                        decode_metas[i],
                        path_prefix_registry=self._path_registry,
                    )
                )

            else:
                logger.info(f"[{path}] Stage 3: New Cluster detected. Triggering VLM. (Dist: {min_distance:.3f})")
                self._create_new_cluster(path, vector, images[i], decode_metas[i])
                stats["vlm_calls"] += 1

        # 4. 批量写入 [阶段 2] 收集到的同簇数据
        if ids_to_add:
            self.db.add_batch(ids_to_add, embs_to_add, metas_to_add)

        return stats

    def _generate_unique_cluster_id(self) -> str:
        """生成一个在数据库中不存在的唯一 8 位 Cluster ID"""
        while True:
            new_id = f"cls_{uuid.uuid4().hex[:8]}"
            existing = self.db.collection.get(
                where={"cluster_id": new_id},
                limit=1
            )
            if not existing['ids']:
                return new_id
            logger.warning(f"Collision detected for cluster_id: {new_id}, regenerating...")

    def _create_new_cluster(
        self,
        image_path: str,
        vector: List[float],
        image: Image.Image,
        decode_meta: Dict[str, Any],
    ) -> Tuple[str, str]:
        """
        处理新场景：创建新簇，触发 VLM 标注，并将新中心点插入数据库。
        """
        cluster_id = self._generate_unique_cluster_id()

        try:
            labels_dict = self.vlm.annotate_image(image)
            labels_json = json.dumps(labels_dict, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to get VLM annotation for {image_path}, using empty labels. Error: {e}")
            labels_json = "{}"

        doc_id = str(uuid.uuid4())
        self.db.add_batch(
            ids=[doc_id],
            embeddings=[vector],
            metadatas=[
                self._row_meta(
                    image_path,
                    cluster_id,
                    True,
                    labels_json,
                    decode_meta,
                    path_prefix_registry=self._path_registry,
                )
            ],
        )
        return cluster_id, labels_json
