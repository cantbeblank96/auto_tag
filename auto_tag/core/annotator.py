import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from PIL import Image

from auto_tag.core.cluster_engine import ClusterEngine
from auto_tag.core.config import settings
from auto_tag.core.duplicate_store import DuplicateLinkWriter
from auto_tag.core.feature_extractor import FeatureExtractor
from auto_tag.core.image_load_context import ImageLoadContext
from auto_tag.core.path_prefix_registry import PathPrefixRegistry
from auto_tag.core.pipeline_profile import PipelineProfile
from auto_tag.core.vector_db import VectorDB
from auto_tag.core.vlm_annotation_pool import AnnotationJob, VlmAnnotationPool
from auto_tag.core.vlm_client import VLMClient

logger = logging.getLogger(__name__)


class ImageAutoAnnotator:
    """
    图像自动标注门面：CLIP 建簇（ClusterEngine）与 VLM 标注（VlmAnnotationPool）解耦并行。

    生命周期：
      1. start_vlm_pool() — 流水线开始前启动全局 VLM worker
      2. process_batch() — 仅 CLIP + 建簇，新簇中心异步入队
      3. shutdown_vlm_pool() — 流水线结束后 drain 并关闭 worker
    """

    def __init__(
        self,
        duplicate_link_writer: Optional[DuplicateLinkWriter] = None,
        *,
        db_path: Optional[str] = None,
        path_prefix_registry: Optional[PathPrefixRegistry] = None,
        load_context: Optional[ImageLoadContext] = None,
    ):
        logger.info("Initializing Image Auto Annotator (decoupled CLIP + VLM)...")
        self.extractor = FeatureExtractor(
            model_name=settings.clip_model_name,
            device=settings.device,
        )
        self.db = VectorDB(
            db_path=db_path if db_path is not None else settings.db_path,
            collection_name=settings.collection_name,
        )
        self.vlm = self._create_vlm_client()
        self.duplicate_link_writer = duplicate_link_writer
        self._path_registry = path_prefix_registry
        self._load_context = load_context or ImageLoadContext()
        self._db_lock = threading.Lock()

        self._cluster = ClusterEngine(
            self.db,
            tau_dup=settings.tau_dup,
            tau_cls=settings.tau_cls,
            duplicate_link_writer=duplicate_link_writer,
            path_prefix_registry=path_prefix_registry,
            db_lock=self._db_lock,
        )
        self._vlm_pool: Optional[VlmAnnotationPool] = None
        self._vlm_done_callback: Optional[Callable[[], None]] = None

    @staticmethod
    def _create_vlm_client():
        """根据配置创建 VLMClient：多模型 failover 或单模型。"""
        from auto_tag.core.circuit_breaker import get_circuit_breaker, CircuitBreakerConfig

        models = getattr(settings, "vlm_models", None)
        if models and len(models) > 0:
            cb_config = CircuitBreakerConfig(
                time_window_seconds=settings.circuit_breaker_time_window,
                failure_rate_threshold=settings.circuit_breaker_failure_threshold,
                cooldown_seconds=settings.circuit_breaker_cooldown,
            )
            cb = get_circuit_breaker()
            cb.update_config(cb_config)
            return VLMClient(models=models, circuit_breaker=cb)
        return VLMClient(
            model_name=settings.vlm_model_name,
            api_key=settings.vlm_api_key,
        )

    @staticmethod
    def _row_meta(*args, **kwargs) -> Dict[str, Any]:
        """兼容 records.py 等外部引用。"""
        return ClusterEngine.row_meta(*args, **kwargs)

    def set_load_context(self, ctx: ImageLoadContext) -> None:
        self._load_context = ctx
        if self._vlm_pool is not None:
            self._vlm_pool.load_context = ctx

    def start_vlm_pool(
        self,
        *,
        profile: Optional[PipelineProfile] = None,
        on_vlm_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """启动全局 VLM 标注池（流水线入口调用一次）。"""
        if self._vlm_pool is not None:
            return
        self._vlm_done_callback = on_vlm_done
        self._vlm_pool = VlmAnnotationPool(
            vlm=self.vlm,
            db=self.db,
            db_lock=self._db_lock,
            load_context=self._load_context,
            profile=profile,
            on_vlm_done=on_vlm_done,
        )
        self._vlm_pool.start()

    def shutdown_vlm_pool(
        self,
        *,
        wait: bool = True,
        cancel_pending: bool = False,
    ) -> None:
        """关闭 VLM 池；默认等待在途与队列任务完成。"""
        if self._vlm_pool is None:
            return
        if wait:
            self._vlm_pool.wait_idle()
        self._vlm_pool.shutdown(wait=wait, cancel_pending=cancel_pending)
        self._vlm_pool = None

    def _submit_vlm_job(self, job: AnnotationJob) -> None:
        if self._vlm_pool is None:
            raise RuntimeError("VLM pool not started; call start_vlm_pool() first")
        self._vlm_pool.submit(job)

    def process_batch(
        self,
        valid_paths: List[str],
        images: List[Image.Image],
        *,
        decode_metas: Optional[List[Dict[str, Any]]] = None,
        profile: Optional[PipelineProfile] = None,
        on_item_done: Optional[Callable[[Dict[str, int]], None]] = None,
    ) -> Dict[str, int]:
        """
        批量 CLIP 建簇；VLM 在后台池异步执行。

        on_item_done 增量字段：
          stage1_skips / stage2_joins / new_centers — 建簇完成时回调
          vlm_calls — 由 pipeline 通过 on_vlm_done 在 VLM 完成时单独累计
        """
        zero = {"vlm_calls": 0, "stage1_skips": 0, "stage2_joins": 0, "new_centers": 0}
        if not valid_paths or not images:
            return dict(zero)

        prof = profile or PipelineProfile(False)

        with prof.span("clip_extract_batch"):
            embeddings = self.extractor.extract_features_batch(images)
        if not embeddings:
            return dict(zero)

        stats, _jobs = self._cluster.process_batch(
            valid_paths,
            embeddings,
            decode_metas=decode_metas,
            profile=prof,
            on_item_done=on_item_done,
            on_vlm_job=self._submit_vlm_job,
        )
        return {
            "vlm_calls": 0,
            "stage1_skips": stats.get("stage1_skips", 0),
            "stage2_joins": stats.get("stage2_joins", 0),
            "new_centers": stats.get("new_centers", 0),
        }
