"""
标注流水线：收集路径、可选校验样图、分批加载与调用 ImageAutoAnnotator。
供 CLI（main）与 HTTP 后端共用。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from kevin_toolbox.data_flow.file import json_
from kevin_toolbox.computer_science.algorithm.for_seq import chunk_generator

from auto_tag.core.config import settings
from auto_tag.core.duplicate_store import DuplicateLinkWriter
from auto_tag.core.path_prefix_registry import PathPrefixRegistry
from auto_tag.core.utils.load_image import load_image_for_job

logger = logging.getLogger(__name__)

# auto_tag 包根目录（与 config.json 同级）
_AUTO_TAG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _walk_collect_images(input_dir: str, suffix_ls: List[str]) -> List[str]:
    """递归收集目录下匹配后缀的文件（支持多层子目录）。"""
    suffix_lower = tuple(s.lower() for s in suffix_ls)
    out: List[str] = []
    for dirpath, _, filenames in os.walk(input_dir):
        for name in filenames:
            low = name.lower()
            if any(low.endswith(s) for s in suffix_lower):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


# 与 main 保持一致，并补充常见后缀
DEFAULT_IMAGE_SUFFIXES = [
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".yuv",
    ".nv21",
    ".nv12",
]

_YUV_SUFFIXES = (".yuv", ".nv21", ".nv12")


def decode_meta_for_path(path: str, cfg: "PipelineConfig") -> Dict[str, Any]:
    """写入向量库的解码提示（按路径预览 YUV 时使用）。"""
    low = path.lower()
    treat_yuv = cfg.b_yuv_image or (
        cfg.mixed_yuv and any(low.endswith(s) for s in _YUV_SUFFIXES)
    )
    if treat_yuv:
        return {
            "media_kind": "yuv",
            "pix_w": int(cfg.image_width or 0),
            "pix_h": int(cfg.image_height or 0),
            "yuv_layout": str(cfg.yuv_type or "nv21"),
        }
    return {"media_kind": "rgb", "pix_w": 0, "pix_h": 0, "yuv_layout": ""}


def normalize_work_dir(work_dir: str) -> str:
    """工作根目录：去空白、展开 ~、{PROJECT_PATH} 宏、绝对路径 + realpath，避免线程内相对路径错误。"""
    s = (work_dir or "").strip()
    # 替换 {PROJECT_PATH} 宏
    s = s.replace("{PROJECT_PATH}", _AUTO_TAG_DIR)
    if not s:
        s = os.path.join(_AUTO_TAG_DIR, "work_dir")
    return os.path.realpath(os.path.abspath(os.path.expanduser(s)))


def work_log_dir(work_dir: str) -> str:
    """日志目录：work_dir/log。"""
    return os.path.join(normalize_work_dir(work_dir), "log")


def work_embedding_store_dir(work_dir: str) -> str:
    """向量索引持久化目录：work_dir/{embedding_subdir}；若仅有旧版 chroma_data 则自动使用。"""
    w = normalize_work_dir(work_dir)
    sub = (settings.embedding_subdir or "embedding_index").strip().strip("/\\")
    new_p = os.path.join(w, sub)
    legacy = os.path.join(w, "chroma_data")
    if os.path.isdir(legacy) and not os.path.isdir(new_p):
        return legacy
    return new_p


def work_chroma_dir(work_dir: str) -> str:
    """兼容旧名：等同于 work_embedding_store_dir。"""
    return work_embedding_store_dir(work_dir)


# 旧名称别名（脚本/外部引用）
normalize_output_dir = normalize_work_dir
output_log_dir = work_log_dir
output_chroma_dir = work_embedding_store_dir


@dataclass
class PipelineConfig:
    """单次标注任务参数。"""

    input_dirs: List[str] = field(default_factory=list)
    image_ls_files: List[str] = field(default_factory=list)
    work_dir: str = ""
    """空字符串表示使用默认路径（normalize_work_dir 时会解析为 auto_tag/work_dir）。"""
    rotate_angle: Optional[str] = None
    b_yuv_image: bool = False
    """整批均为 YUV 时使用。"""
    mixed_yuv: bool = False
    """同一目录混合 JPG 与 .nv21 等时开启，按后缀自动选择解码方式。"""
    yuv_type: str = "nv21"
    image_height: int = 0
    image_width: int = 0
    batch_size: Optional[int] = None
    record_stage1_duplicates: Optional[bool] = None
    """None 表示使用 settings.record_stage1_duplicates。"""
    skip_if_in_db: bool = False
    """为 True 时若向量索引中已有同 image_path 则跳过；为 False 时先删旧记录再重跑（覆盖）。"""


@dataclass
class PipelineResult:
    total_images: int
    failed_paths: List[str]
    processed_ok: int


def collect_image_paths(
    input_dirs: List[str],
    image_ls_files: List[str],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    返回 (全部图片路径列表, 用于校验样图的来源信息列表)。
    all_sources 每项: {"name": str, "sample_path": str}
    """
    all_sources: List[Dict[str, Any]] = []
    image_list: List[str] = []

    for d in input_dirs:
        if os.path.isdir(d):
            imgs = _walk_collect_images(d, DEFAULT_IMAGE_SUFFIXES)
            if imgs:
                all_sources.append({"name": os.path.basename(d), "sample_path": imgs[0]})
                image_list.extend(imgs)
        else:
            logger.warning("Input dir not found or not a directory: %s", d)

    for f_path in image_ls_files:
        if os.path.exists(f_path):
            try:
                imgs = json_.read(file_path=f_path, b_use_suggested_converter=True)
                if isinstance(imgs, list) and imgs:
                    all_sources.append({"name": os.path.basename(f_path), "sample_path": imgs[0]})
                    image_list.extend(imgs)
            except Exception as e:
                logger.error("Failed to load image list file %s: %s", f_path, e)
        else:
            logger.warning("Image list file not found: %s", f_path)

    return image_list, all_sources


def save_verify_samples(
    all_sources: List[Dict[str, Any]],
    log_dir: str,
    cfg: PipelineConfig,
) -> None:
    """将每个来源首张样图保存到 log 子目录下的 verify_*.png。"""
    os.makedirs(log_dir, exist_ok=True)
    for source in all_sources:
        sample_path = source["sample_path"]
        try:
            sample_img = load_image_for_job(
                sample_path,
                b_yuv_image=cfg.b_yuv_image,
                mixed_yuv=cfg.mixed_yuv,
                yuv_type=cfg.yuv_type,
                image_height=cfg.image_height,
                image_width=cfg.image_width,
                rotate_angle=cfg.rotate_angle,
            )
            verify_path = os.path.join(log_dir, f"verify_{source['name']}.png")
            sample_img.save(verify_path)
            logger.info("Sample image from %s saved to %s", source["name"], verify_path)
        except Exception as e:
            logger.error("Failed to load sample image from %s: %s", source["name"], e)


def run_annotation_pipeline(
    cfg: PipelineConfig,
    *,
    on_progress: Optional[Callable[..., None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> PipelineResult:
    """
    执行完整标注流水线（不含终端交互式确认）。

    Args:
        cfg: 任务配置
        on_progress: 回调，签名为
          ``(done: int, total: int, failed_n: int, *, skip_in_db: int, vlm_calls: int,
          stage1_skips: int, stage2_joins: int) -> None``（后四个为累计值）。
        should_cancel: 若返回 True 则尽快结束循环（当前 batch 仍会跑完）
    """
    cfg = replace(cfg, work_dir=normalize_work_dir(cfg.work_dir))

    image_list, _ = collect_image_paths(cfg.input_dirs, cfg.image_ls_files)
    if not image_list:
        logger.warning("No images found to process.")
        return PipelineResult(total_images=0, failed_paths=[], processed_ok=0)

    out_root = normalize_work_dir(cfg.work_dir)
    log_d = os.path.join(out_root, "log")
    emb_d = work_embedding_store_dir(cfg.work_dir)
    os.makedirs(log_d, exist_ok=True)
    os.makedirs(emb_d, exist_ok=True)

    path_registry = PathPrefixRegistry(log_d)
    for d in cfg.input_dirs:
        if d and os.path.isdir(d):
            path_registry.register_abs_dir(d)
    for lf in cfg.image_ls_files:
        if lf and os.path.isfile(lf):
            path_registry.register_abs_dir(os.path.dirname(os.path.abspath(lf)))

    batch_size = cfg.batch_size if cfg.batch_size is not None else settings.batch_size
    record_dup = (
        cfg.record_stage1_duplicates
        if cfg.record_stage1_duplicates is not None
        else settings.record_stage1_duplicates
    )
    dup_writer: Optional[DuplicateLinkWriter] = None
    if record_dup:
        dup_writer = DuplicateLinkWriter(
            log_d, path_registry, filename=settings.duplicate_links_filename
        )

    from auto_tag.core.annotator import ImageAutoAnnotator

    annotator = ImageAutoAnnotator(
        duplicate_link_writer=dup_writer,
        db_path=emb_d,
        path_prefix_registry=path_registry,
    )
    failed_images: List[str] = []
    processed_ok = 0
    total = len(image_list)
    images_seen = 0
    skip_in_db_n = 0
    vlm_total = 0
    stage1_total = 0
    stage2_total = 0

    def _emit_progress() -> None:
        if on_progress:
            on_progress(
                min(images_seen, total),
                total,
                len(failed_images),
                skip_in_db=skip_in_db_n,
                vlm_calls=vlm_total,
                stage1_skips=stage1_total,
                stage2_joins=stage2_total,
            )

    logger.info("Total images to process: %d", total)

    for batch_paths in chunk_generator(
        inputs=image_list,
        chunk_size=batch_size,
        b_drop_last=False,
        b_display_progress=True,
    ):
        valid_paths_in_batch: List[str] = []
        loaded_images = []
        batch_cancelled = False

        for path in batch_paths:
            if should_cancel and should_cancel():
                logger.info("Pipeline cancelled by request.")
                batch_cancelled = True
                break

            if cfg.skip_if_in_db and annotator.db.has_image_path(
                path, registry=path_registry
            ):
                skip_in_db_n += 1
                images_seen += 1
                _emit_progress()
                continue

            if not cfg.skip_if_in_db:
                try:
                    annotator.db.delete_by_image_path(path, registry=path_registry)
                except Exception as e:
                    logger.warning("delete_by_image_path %s: %s", path, e)

            try:
                img = load_image_for_job(
                    path,
                    b_yuv_image=cfg.b_yuv_image,
                    mixed_yuv=cfg.mixed_yuv,
                    yuv_type=cfg.yuv_type,
                    image_height=cfg.image_height,
                    image_width=cfg.image_width,
                    rotate_angle=cfg.rotate_angle,
                )
                valid_paths_in_batch.append(path)
                loaded_images.append(img)
            except Exception as e:
                logger.error("Failed to load image %s: %s", path, e)
                failed_images.append(path)
            images_seen += 1
            _emit_progress()

        if batch_cancelled:
            break

        if valid_paths_in_batch:
            try:
                decode_metas = [
                    decode_meta_for_path(p, cfg) for p in valid_paths_in_batch
                ]
                bstat = annotator.process_batch(
                    valid_paths_in_batch, loaded_images, decode_metas=decode_metas
                )
                vlm_total += int(bstat.get("vlm_calls", 0))
                stage1_total += int(bstat.get("stage1_skips", 0))
                stage2_total += int(bstat.get("stage2_joins", 0))
                processed_ok += len(valid_paths_in_batch)
            except Exception as e:
                logger.error("Batch processing error: %s", e)
                failed_images.extend(valid_paths_in_batch)

        _emit_progress()

    if failed_images:
        failed_file = os.path.join(log_d, "failed_images.json")
        json_.write(
            content=failed_images,
            file_path=failed_file,
            b_use_suggested_converter=True,
        )
        logger.warning(
            "%d images failed to process. List saved to %s",
            len(failed_images),
            failed_file,
        )

    logger.info("Pipeline finished. processed_ok=%s failed=%s", processed_ok, len(failed_images))
    return PipelineResult(
        total_images=total,
        failed_paths=failed_images,
        processed_ok=processed_ok,
    )
