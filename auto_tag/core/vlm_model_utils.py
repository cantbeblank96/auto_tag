"""VLM 多模型配置工具：端点 id 与 provider 模型名分离。"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List


def vlm_model_endpoint_id(model: Dict[str, Any], index: int = 0) -> str:
    """返回用于熔断/测试/UI 区分的端点 id（可与 API 的 model 名相同但不必唯一）。

    优先使用配置中的 ``id``；否则用列表下标 + 名称，避免同名多账号共用状态。
    """
    existing = str(model.get("id") or "").strip()
    if existing:
        return existing
    name = str(model.get("name") or "unknown")
    return f"idx-{index}::{name}"


def ensure_vlm_model_ids(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为缺少 id 的模型条目生成 UUID（写回 config 前调用）。"""
    out: List[Dict[str, Any]] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        item = dict(m)
        if not str(item.get("id") or "").strip():
            item["id"] = str(uuid.uuid4())
        out.append(item)
    return out


def iter_enabled_vlm_models(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤 enabled=false 的条目（缺省视为启用）。"""
    enabled: List[Dict[str, Any]] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        if m.get("enabled") is False:
            continue
        enabled.append(m)
    return enabled
