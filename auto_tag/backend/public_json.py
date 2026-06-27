"""对外 JSON 展示：隐藏 path_prefix_id / image_rel_path，统一用绝对路径 image_path。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from auto_tag.core.path_prefix_registry import PathPrefixRegistry, resolve_stored_image_path


def public_duplicate_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """侧车单行：仅保留业务可读字段（绝对路径已由 store 读出时拼好）。"""
    return {
        "anchor_id": row.get("anchor_id"),
        "anchor_path": row.get("anchor_path"),
        "dup_path": row.get("dup_path"),
        "distance": row.get("distance"),
        "ts": row.get("ts"),
    }


def public_chroma_metadata(
    meta: Dict[str, Any], *, registry: Optional[PathPrefixRegistry] = None
) -> Dict[str, Any]:
    """Chroma 元数据：去掉前缀压缩字段，只保留 image_path（绝对路径）。"""
    skip = frozenset({"path_prefix_id", "image_rel_path"})
    out: Dict[str, Any] = {}
    for k, v in meta.items():
        if k in skip:
            continue
        if k == "image_path":
            continue
        out[k] = v
    if registry is not None:
        abs_p = resolve_stored_image_path(meta, registry) or ""
    else:
        abs_p = ""
    if not abs_p:
        abs_p = str(meta.get("image_path") or "")
    out["image_path"] = abs_p
    return out
