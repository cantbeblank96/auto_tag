"""
标注流水线：收集路径、可选校验样图、分批加载与调用 ImageAutoAnnotator。
供 CLI（main）与 HTTP 后端共用。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from kevin_toolbox.data_flow.file import json_
from kevin_toolbox.computer_science.algorithm.for_seq import chunk_generator

from auto_tag.core.config import settings
from auto_tag.core.duplicate_store import DuplicateLinkWriter, load_sidecar_known_paths
from auto_tag.core.image_load_context import ImageLoadContext
from auto_tag.core.path_prefix_registry import PathPrefixRegistry
from auto_tag.core.pipeline_profile import PipelineProfile, resolve_pipeline_debug
from auto_tag.core.vlm_timing_collector import configure as timing_configure
from auto_tag.core.vlm_timing_collector import resolve_enabled as timing_resolve_enabled
from auto_tag.core.vlm_timing_collector import record as timing_record
from auto_tag.core.vlm_timing_collector import save_json as timing_save_json
from auto_tag.core.vlm_timing_report import write_debug_artifacts
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


def normalize_image_suffixes(
    raw_suffixes: Optional[List[str]], lowercase: bool = True
) -> List[str]:
    """后缀过滤条件归一化：去空白、自动补前导点、去重；lowercase=False 时保留原大小写。"""
    out: List[str] = []
    seen = set()
    for raw in raw_suffixes or []:
        s = str(raw or "").strip()
        if lowercase:
            s = s.lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


@dataclass
class ImageFilterSpec:
    """目录扫描阶段的文件名过滤条件（对 image_ls 显式列表不生效）。"""

    suffixes: List[str] = field(default_factory=list)
    """后缀模式：已归一化的后缀列表（如 [".jpg"]）；为空表示不按后缀过滤。"""
    name_regex: Optional[str] = None
    """正则模式：非空时优先于 suffixes，匹配文件名或完整路径。"""
    ignore_case: bool = True
    match_full_path: bool = False
    """为 True 时正则匹配完整路径，否则仅匹配文件名。"""

    def is_active(self) -> bool:
        return bool(self.name_regex) or bool(self.suffixes)


def build_image_filter_spec(
    image_suffixes: Optional[List[str]] = None,
    image_name_regex: Optional[str] = None,
    filter_ignore_case: bool = True,
    filter_match_full_path: bool = False,
) -> ImageFilterSpec:
    """由任务参数构造过滤条件；正则非法或后缀全部无效时抛 ValueError。"""
    regex = (image_name_regex or "").strip() or None
    if regex is not None:
        flags = re.IGNORECASE if filter_ignore_case else 0
        try:
            re.compile(regex, flags)
        except re.error as e:
            raise ValueError(f"image_name_regex 非法：{e}")
        return ImageFilterSpec(
            name_regex=regex,
            ignore_case=bool(filter_ignore_case),
            match_full_path=bool(filter_match_full_path),
        )
    suffixes = normalize_image_suffixes(image_suffixes, lowercase=bool(filter_ignore_case))
    if image_suffixes and not suffixes:
        raise ValueError("image_suffixes 归一化后为空")
    return ImageFilterSpec(suffixes=suffixes, ignore_case=bool(filter_ignore_case))


def _apply_image_filter(paths: List[str], spec: Optional[ImageFilterSpec]) -> List[str]:
    """按过滤条件筛选路径列表。

    正则在此生效；后缀仅在区分大小写时在此二次筛选
    （忽略大小写的后缀匹配已在目录扫描阶段完成）。
    """
    if spec is None or not spec.is_active():
        return paths
    if spec.name_regex:
        flags = re.IGNORECASE if spec.ignore_case else 0
        pattern = re.compile(spec.name_regex, flags)
        out = []
        for p in paths:
            target = p if spec.match_full_path else os.path.basename(p)
            if pattern.search(target):
                out.append(p)
        return out
    if not spec.ignore_case:
        out = []
        for p in paths:
            name = os.path.basename(p)
            if any(name.endswith(s) for s in spec.suffixes):
                out.append(p)
        return out
    return paths


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
    image_suffixes: Optional[List[str]] = None
    """后缀过滤（仅作用于 input_dirs 扫描）；None/空 = 不过滤。"""
    image_name_regex: Optional[str] = None
    """文件名正则过滤；非空时优先于 image_suffixes。"""
    filter_ignore_case: bool = True
    """过滤时是否忽略大小写（默认忽略）。"""
    filter_match_full_path: bool = False
    """正则匹配完整路径还是仅文件名（默认仅文件名）。"""
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
    pipeline_debug: Optional[bool] = None
    """为 True 时输出各阶段耗时；None 时使用 settings.pipeline_debug / 环境变量。"""


@dataclass
class PipelineResult:
    total_images: int
    failed_paths: List[str]
    processed_ok: int
    profile_summary: Optional[Dict[str, Any]] = None
    # 簇中心 VLM 标注失败的图片路径（供“重跑失败部分”使用）
    vlm_failed_paths: List[str] = field(default_factory=list)


def _read_image_list(path: str) -> List[str]:
    """读取图片路径列表文件；强制 UTF-8，避免 Windows 默认 GBK 解码中文路径失败。

    支持两种格式：
    - 旧格式：整个文件为 JSON 数组（向后兼容）；
    - v2 格式：首个非空行为 JSON 头部（含 prefix / image_num 等字段），
      其余行每行一个路径；相对行与 prefix 拼接，绝对行原样使用。
      image_num 与实际有效行数不一致时仅告警不中断。
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    prefix = ""
    image_num: Optional[int] = None
    start = 0
    if lines and lines[0].startswith("{"):
        try:
            header = json.loads(lines[0])
        except json.JSONDecodeError as e:
            raise ValueError(f"image_ls 头部行 JSON 解析失败：{e}")
        if not isinstance(header, dict):
            raise ValueError("image_ls 头部行必须是 JSON 对象")
        prefix = str(header.get("prefix") or "")
        raw_num = header.get("image_num")
        if raw_num is not None:
            try:
                image_num = int(raw_num)
            except (TypeError, ValueError):
                raise ValueError(f"image_ls 头部 image_num 非整数：{raw_num!r}")
        start = 1
    out: List[str] = []
    skipped = 0
    for ln in lines[start:]:
        if os.path.isabs(ln):
            out.append(ln)
        elif prefix:
            out.append(os.path.join(prefix, ln))
        else:
            skipped += 1
    if skipped:
        logger.warning(
            "image_ls %s: 跳过 %d 行相对路径（无 prefix 可拼接）", path, skipped
        )
    if image_num is not None and len(out) != image_num:
        logger.warning(
            "image_ls %s: image_num=%d 与实际有效行数 %d 不一致，按实际行处理",
            path, image_num, len(out),
        )
    return out


# 旧名兼容
_read_image_list_json = _read_image_list


def _write_image_list_json(path: str, paths: List[str]) -> None:
    """写入图片路径列表 JSON（UTF-8）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(paths, f, ensure_ascii=False, indent=4)


def collect_image_paths(
    input_dirs: List[str],
    image_ls_files: List[str],
    filter_spec: Optional[ImageFilterSpec] = None,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    返回 (全部图片路径列表, 用于校验样图的来源信息列表)。
    all_sources 每项: {"name": str, "sample_path": str}

    filter_spec 仅作用于 input_dirs 目录扫描；image_ls 显式列表不参与过滤。
    后缀模式下直接用过滤后缀扫描（可命中 .jpg 等默认后缀之外的类型）；
    正则模式下先按默认后缀扫描再按正则筛选。
    """
    all_sources: List[Dict[str, Any]] = []
    image_list: List[str] = []
    active = filter_spec is not None and filter_spec.is_active()
    if active and filter_spec.suffixes and not filter_spec.name_regex:
        scan_suffixes = filter_spec.suffixes
    else:
        scan_suffixes = DEFAULT_IMAGE_SUFFIXES

    for d in input_dirs:
        if os.path.isdir(d):
            imgs = _walk_collect_images(d, scan_suffixes)
            imgs = _apply_image_filter(imgs, filter_spec)
            if imgs:
                all_sources.append({"name": os.path.basename(d), "sample_path": imgs[0]})
                image_list.extend(imgs)
        else:
            logger.warning("Input dir not found or not a directory: %s", d)

    for f_path in image_ls_files:
        if os.path.exists(f_path):
            try:
                imgs = _read_image_list(f_path)
                if imgs:
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
          vlm_failed: int, new_centers: int, stage1_skips: int, stage2_joins: int) -> None``
          （``done`` 为建簇阶段已处理张数；``vlm_calls/new_centers`` 为 VLM 成功/待标簇中心数；
          ``vlm_failed`` 为 VLM 失败次数）。
        should_cancel: 若返回 True 则尽快结束循环（当前 batch 仍会跑完）
    """
    cfg = replace(cfg, work_dir=normalize_work_dir(cfg.work_dir))
    profile = PipelineProfile(resolve_pipeline_debug(cfg.pipeline_debug))
    profile.mark_wall_start()

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
    timing_on = timing_resolve_enabled(cfg.pipeline_debug)
    timing_configure(
        enabled=timing_on,
        meta={
            "vlm_concurrency": int(getattr(settings, "vlm_concurrency", 1) or 1),
            "vlm_http_timeout": float(getattr(settings, "vlm_http_timeout", 60) or 60),
            "batch_size": int(batch_size),
            "total_images": len(image_list),
        },
    )
    if timing_on:
        timing_record("pipeline_start")
    record_dup = (
        cfg.record_stage1_duplicates
        if cfg.record_stage1_duplicates is not None
        else settings.record_stage1_duplicates
    )
    dup_writer: Optional[DuplicateLinkWriter] = None
    dup_store_path = os.path.join(log_d, settings.duplicate_links_filename)
    if record_dup:
        dup_writer = DuplicateLinkWriter(
            log_d, path_registry, filename=settings.duplicate_links_filename
        )
    # skip_if_in_db：除向量库路径外，侧车已登记的近重复路径也应跳过（近重复默认不入库）
    sidecar_known_paths = set()
    if cfg.skip_if_in_db:
        sidecar_known_paths = load_sidecar_known_paths(dup_store_path, log_dir=log_d)
        if sidecar_known_paths:
            logger.info(
                "skip_if_in_db: loaded %d paths from duplicate sidecar",
                len(sidecar_known_paths),
            )

    from auto_tag.core.annotator import ImageAutoAnnotator

    with profile.span("annotator_init"):
        load_ctx = ImageLoadContext(
            b_yuv_image=cfg.b_yuv_image,
            mixed_yuv=cfg.mixed_yuv,
            yuv_type=cfg.yuv_type,
            image_height=cfg.image_height,
            image_width=cfg.image_width,
            rotate_angle=cfg.rotate_angle,
        )
        annotator = ImageAutoAnnotator(
            duplicate_link_writer=dup_writer,
            db_path=emb_d,
            path_prefix_registry=path_registry,
            load_context=load_ctx,
        )

    failed_images: List[str] = []
    vlm_failed_paths: List[str] = []
    processed_ok = 0
    total = len(image_list)
    images_seen = 0
    skip_in_db_n = 0
    vlm_total = 0
    vlm_failed_total = 0
    new_centers_total = 0
    stage1_total = 0
    stage2_total = 0
    pipeline_cancelled = False

    def _emit_progress() -> None:
        if on_progress:
            on_progress(
                min(images_seen, total),
                total,
                len(failed_images),
                skip_in_db=skip_in_db_n,
                vlm_calls=vlm_total,
                vlm_failed=vlm_failed_total,
                new_centers=new_centers_total,
                stage1_skips=stage1_total,
                stage2_joins=stage2_total,
            )

    vlm_progress_lock = threading.Lock()

    def _on_vlm_done() -> None:
        nonlocal vlm_total
        with vlm_progress_lock:
            vlm_total += 1
        _emit_progress()

    def _on_vlm_failed(path: str) -> None:
        nonlocal vlm_failed_total
        with vlm_progress_lock:
            vlm_failed_total += 1
            if path and path not in vlm_failed_paths:
                vlm_failed_paths.append(path)
        _emit_progress()

    # 启动前检查：占位/空 API Key 会导致打标全失败（打标数显示为 0）
    _placeholder_keys = []
    for _m in list(getattr(settings, "vlm_models", None) or []):
        if not isinstance(_m, dict):
            continue
        _k = str(_m.get("api_key") or "").strip()
        _name = str(_m.get("name") or "")
        if (not _k) or (_k.lower() in {"your_api_key_here", "changeme", "xxx"}):
            _placeholder_keys.append(_name or "(unnamed)")
    if _placeholder_keys:
        logger.error(
            "VLM api_key 疑似未配置（占位符/空）: %s。任务会建簇但标注为 0。请到设置页填写真实 API Key 并重置熔断。",
            ", ".join(_placeholder_keys),
        )

    annotator.start_vlm_pool(
        profile=profile, on_vlm_done=_on_vlm_done, on_vlm_failed=_on_vlm_failed
    )

    try:
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
                    pipeline_cancelled = True
                    break

                if cfg.skip_if_in_db:
                    norm = os.path.realpath(
                        os.path.abspath(os.path.expanduser(str(path).strip()))
                    )
                    in_chroma = annotator.db.has_image_path(
                        path, registry=path_registry
                    )
                    in_sidecar = norm in sidecar_known_paths
                    if in_chroma or in_sidecar:
                        skip_in_db_n += 1
                        images_seen += 1
                        _emit_progress()
                        continue

                if not cfg.skip_if_in_db:
                    try:
                        t0 = time.perf_counter() if profile.enabled else 0.0
                        annotator.db.delete_by_image_path(path, registry=path_registry)
                        if profile.enabled:
                            profile.add("db_delete_path", time.perf_counter() - t0)
                    except Exception as e:
                        logger.warning("delete_by_image_path %s: %s", path, e)

                try:
                    t0 = time.perf_counter() if profile.enabled else 0.0
                    img = load_image_for_job(
                        path,
                        b_yuv_image=cfg.b_yuv_image,
                        mixed_yuv=cfg.mixed_yuv,
                        yuv_type=cfg.yuv_type,
                        image_height=cfg.image_height,
                        image_width=cfg.image_width,
                        rotate_angle=cfg.rotate_angle,
                    )
                    if profile.enabled:
                        profile.add("load_image", time.perf_counter() - t0)
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
                batch_items_done = 0

                def _on_item_done(delta: Dict[str, int]) -> None:
                    nonlocal images_seen, new_centers_total, stage1_total, stage2_total, batch_items_done
                    images_seen += 1
                    batch_items_done += 1
                    new_centers_total += int(delta.get("new_centers", 0))
                    stage1_total += int(delta.get("stage1_skips", 0))
                    stage2_total += int(delta.get("stage2_joins", 0))
                    _emit_progress()

                try:
                    decode_metas = [
                        decode_meta_for_path(p, cfg) for p in valid_paths_in_batch
                    ]
                    annotator.process_batch(
                        valid_paths_in_batch,
                        loaded_images,
                        decode_metas=decode_metas,
                        profile=profile,
                        on_item_done=_on_item_done,
                    )
                    processed_ok += len(valid_paths_in_batch)
                    timing_record(
                        "cluster_batch_done",
                        batch_n=len(valid_paths_in_batch),
                        images_seen=images_seen,
                        new_centers=new_centers_total,
                    )
                    remaining = len(valid_paths_in_batch) - batch_items_done
                    if remaining > 0:
                        images_seen += remaining
                        _emit_progress()
                except Exception as e:
                    logger.error("Batch processing error: %s", e)
                    failed_images.extend(valid_paths_in_batch)
                    remaining = len(valid_paths_in_batch) - batch_items_done
                    if remaining > 0:
                        images_seen += remaining
                        _emit_progress()

    finally:
        timing_record("vlm_pool_drain_start")
        with profile.span("vlm_pool_drain"):
            annotator.shutdown_vlm_pool(
                wait=True,
                cancel_pending=pipeline_cancelled,
            )
        timing_record("vlm_pool_drain_done")
        # 池排空后回填：修复成员在中心标完前入簇导致的空标签（B2 竞态）
        try:
            with profile.span("backfill_pending_labels"):
                backfilled = annotator.backfill_pending_labels()
            if backfilled:
                timing_record("backfill_pending_labels", fixed=backfilled)
        except Exception:
            logger.exception("backfill_pending_labels failed at pipeline end")

    if failed_images:
        failed_file = os.path.join(log_d, "failed_images.json")
        _write_image_list_json(failed_file, failed_images)
        logger.warning(
            "%d images failed to process. List saved to %s",
            len(failed_images),
            failed_file,
        )

    profile.mark_wall_end()
    profile.log_summary()
    profile_summary = profile.summary() if profile.enabled else None
    if timing_on:
        timing_record("pipeline_end", pipeline_wall=profile_summary.get("pipeline_wall_seconds") if profile_summary else None)
        timing_json_path = os.path.join(log_d, "vlm_timing.json")
        timing_save_json(timing_json_path)
        artifacts = write_debug_artifacts(
            log_d,
            title=f"VLM timing | images={total} ok={processed_ok}",
        )
        written = [f"{k}={v}" for k, v in artifacts.items() if v]
        if written:
            logger.info("Debug timing artifacts: %s", "; ".join(written))
    if profile.enabled and profile_summary:
        profile_path = os.path.join(log_d, "pipeline_profile.json")
        json_.write(
            content=profile_summary,
            file_path=profile_path,
            b_use_suggested_converter=True,
        )
        logger.info("Pipeline profile written to %s", profile_path)

    logger.info("Pipeline finished. processed_ok=%s failed=%s", processed_ok, len(failed_images))
    try:
        from auto_tag.core.vlm_endpoint_stats_store import persist_circuit_breaker_states

        persist_circuit_breaker_states(cfg.work_dir)
    except Exception:
        logger.exception("persist VLM endpoint stats failed at pipeline end")
    return PipelineResult(
        total_images=total,
        failed_paths=failed_images,
        processed_ok=processed_ok,
        profile_summary=profile_summary,
        vlm_failed_paths=list(vlm_failed_paths),
    )
