"""从用户指定的 config.json 合并参数（用于 Web 与快照比对，不重启后端进程）。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from kevin_toolbox.data_flow.file import json_


def merge_stats_params_from_file(
    base: Dict[str, Any],
    config_path: Optional[str],
) -> Dict[str, Any]:
    """将磁盘上的 JSON 配置覆盖到 base 中存在的键（用于数据库页「当前配置」）。"""
    out = dict(base)
    if not config_path or not str(config_path).strip():
        return out
    p = os.path.abspath(os.path.expanduser(str(config_path).strip()))
    if not os.path.isfile(p):
        return out
    try:
        raw = json_.read(file_path=p, b_use_suggested_converter=True)
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out
    if "tau_dup" in raw:
        try:
            out["tau_dup"] = float(raw["tau_dup"])
        except (TypeError, ValueError):
            pass
    if "tau_cls" in raw:
        try:
            out["tau_cls"] = float(raw["tau_cls"])
        except (TypeError, ValueError):
            pass
    if "batch_size" in raw:
        try:
            out["batch_size"] = int(raw["batch_size"])
        except (TypeError, ValueError):
            pass
    if "questions" in raw and isinstance(raw["questions"], dict):
        out["questions"] = dict(raw["questions"])
    if "collection_name" in raw:
        out["collection_name"] = str(raw["collection_name"])
    if "clip_model_name" in raw:
        out["clip_model_name"] = str(raw["clip_model_name"])
    if "vlm_model_name" in raw:
        out["vlm_model_name"] = str(raw["vlm_model_name"])
    if "duplicate_links_filename" in raw:
        out["duplicate_links_filename"] = str(raw["duplicate_links_filename"])
    if "embedding_subdir" in raw:
        out["embedding_subdir"] = str(raw["embedding_subdir"])
    return out
