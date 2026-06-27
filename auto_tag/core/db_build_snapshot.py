"""任务成功结束后写入 work_dir/log，供「数据库」页与当前配置比对。"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from auto_tag.core.config import settings
from auto_tag.core.pipeline import PipelineConfig

logger = logging.getLogger(__name__)

SNAPSHOT_FILENAME = "auto_tag_db_build_snapshot.json"


def snapshot_path(log_dir: str) -> str:
    return os.path.join(log_dir, SNAPSHOT_FILENAME)


def write_build_snapshot(log_dir: str, cfg: PipelineConfig) -> None:
    """将构建时关键参数写入 log 目录（每次任务成功覆盖）。"""
    os.makedirs(log_dir, exist_ok=True)
    payload: Dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "tau_dup": float(settings.tau_dup),
        "tau_cls": float(settings.tau_cls),
        "questions": dict(settings.questions or {}),
        "batch_size": int(settings.batch_size),
        "collection_name": str(settings.collection_name),
        "clip_model_name": str(settings.clip_model_name),
        "vlm_model_name": str(settings.vlm_model_name),
        "duplicate_links_filename": str(settings.duplicate_links_filename),
        "work_dir": str(cfg.work_dir),
        "embedding_subdir": str(settings.embedding_subdir),
        "input_dirs": list(cfg.input_dirs or []),
        "image_ls_files": list(cfg.image_ls_files or []),
        "rotate_angle": cfg.rotate_angle,
        "b_yuv_image": bool(cfg.b_yuv_image),
        "mixed_yuv": bool(cfg.mixed_yuv),
        "yuv_type": str(cfg.yuv_type or "nv21"),
        "image_width": int(cfg.image_width or 0),
        "image_height": int(cfg.image_height or 0),
    }
    path = snapshot_path(log_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("Wrote DB build snapshot to %s", path)
    except OSError as e:
        logger.warning("Failed to write DB build snapshot: %s", e)


def read_build_snapshot(log_dir: str) -> Optional[Dict[str, Any]]:
    p = snapshot_path(log_dir)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read snapshot %s: %s", p, e)
        return None
