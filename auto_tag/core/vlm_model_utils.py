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


def effective_vlm_model_name(
    models: List[Dict[str, Any]], fallback: str = ""
) -> str:
    """实际标注所用 VLM 的展示名：启用端点名称去重后逗号拼接；
    多端点为空时回退单模型配置名（兼容旧配置）。"""
    names: List[str] = []
    for m in iter_enabled_vlm_models(list(models or [])):
        n = str(m.get("name") or "").strip()
        if n and n not in names:
            names.append(n)
    if names:
        return ", ".join(names)
    return str(fallback or "")


def resolve_effective_vlm_model_name() -> str:
    """从当前 settings 解析实际 VLM 展示名（供快照/数据库页比对）。"""
    from auto_tag.core.config import settings

    return effective_vlm_model_name(
        getattr(settings, "vlm_models", None) or [],
        getattr(settings, "vlm_model_name", ""),
    )
