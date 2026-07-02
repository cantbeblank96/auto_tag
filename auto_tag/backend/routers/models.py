"""
models router: 管理 VLM 模型配置 + 熔断状态。

注意：模型配置持久化到 config.json，熔断状态存在于内存（circuit_breaker 单例）。
同名 provider 模型可配置多条（不同 base_url / API key）；以 endpoint ``id`` 区分。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from auto_tag.core.config import settings, _AUTO_TAG_DIR
from auto_tag.core.circuit_breaker import get_circuit_breaker, CircuitBreakerConfig
from auto_tag.core.vlm_model_utils import vlm_model_endpoint_id

router = APIRouter(prefix="/models", tags=["models"])


def _config_json_path() -> str:
    return os.environ.get(
        "AUTO_TAG_CONFIG_FILE",
        os.path.join(_AUTO_TAG_DIR, "config.json"),
    )


def _read_config_json() -> dict:
    path = _config_json_path()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _write_config_json(data: dict) -> None:
    path = _config_json_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _configured_models() -> List[Dict[str, Any]]:
    raw_models = getattr(settings, "vlm_models", None) or []
    if not raw_models:
        raw_models = [
            {
                "name": settings.vlm_model_name,
                "base_url": None,
                "api_key": settings.vlm_api_key,
                "priority": 1,
            }
        ]
    return raw_models


def _run_connectivity_test(model_cfg: Dict[str, Any]) -> Dict[str, Any]:
    model_name = str(model_cfg.get("name") or "")
    base_url = model_cfg.get("base_url") or ""
    api_key = model_cfg.get("api_key") or ""

    url = "https://api.openai.com/v1/chat/completions"
    if base_url:
        url = str(base_url).rstrip("/")
        if url.endswith("/v1"):
            url = f"{url}/chat/completions"
        elif not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Hello, reply with a single word: ok"}],
        "max_tokens": 10,
    }

    start = time.time()
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        elapsed = round((time.time() - start) * 1000)
        content = ""
        try:
            content = data["choices"][0]["message"]["content"][:200] or ""
        except (KeyError, IndexError, TypeError):
            pass
        return {
            "ok": True,
            "latency_ms": elapsed,
            "response": content,
            "model": model_name,
            "endpoint_id": model_cfg.get("id"),
        }
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        return {
            "ok": False,
            "latency_ms": elapsed,
            "error": str(e)[:300],
            "model": model_name,
            "endpoint_id": model_cfg.get("id"),
        }


# ── 模型管理 ──────────────────────────────────────────


@router.get("")
def list_models() -> Dict[str, Any]:
    """获取当前配置的模型列表 + 各端点的熔断状态。"""
    cb = get_circuit_breaker()
    states = cb.get_all_states()
    models_list = []
    for idx, m in enumerate(_configured_models()):
        if not isinstance(m, dict):
            continue
        name = str(m.get("name", ""))
        endpoint_id = vlm_model_endpoint_id(m, idx)
        st = states.get(endpoint_id, {})
        models_list.append({
            "id": m.get("id") or endpoint_id,
            "endpoint_id": endpoint_id,
            "name": name,
            "base_url": m.get("base_url"),
            "api_key": m.get("api_key", ""),
            "priority": int(m.get("priority", 99)),
            "enabled": m.get("enabled", True),
            "tripped": st.get("tripped", False),
            "tripped_until": st.get("tripped_until", 0),
            "failures_in_window": st.get("failures_in_window", 0),
            "total_calls": st.get("total_calls", 0),
            "failure_rate": st.get("failure_rate", 0),
            "last_error": st.get("last_error", ""),
        })
    return {
        "models": models_list,
        "circuit_breaker_config": cb.get_config_dict(),
        "vlm_strategy": getattr(settings, "vlm_strategy", "priority"),
    }


# ── 熔断配置 ──────────────────────────────────────────


class CircuitBreakerConfigBody(BaseModel):
    time_window_seconds: int = Field(300, ge=10, description="监控窗口（秒）")
    failure_rate_threshold: float = Field(0.5, ge=0.0, le=1.0, description="失败率阈值")
    cooldown_seconds: int = Field(600, ge=10, description="停用时长（秒）")


@router.get("/circuit-breaker")
def get_circuit_breaker_config() -> Dict[str, Any]:
    """获取熔断配置 + 各端点实时状态。"""
    cb = get_circuit_breaker()
    return {
        "config": cb.get_config_dict(),
        "states": cb.get_all_states(),
    }


@router.put("/circuit-breaker")
def update_circuit_breaker_config(body: CircuitBreakerConfigBody) -> Dict[str, Any]:
    """更新熔断参数（内存 + config.json）。"""
    cb = get_circuit_breaker()
    cb.update_config(CircuitBreakerConfig(
        time_window_seconds=body.time_window_seconds,
        failure_rate_threshold=body.failure_rate_threshold,
        cooldown_seconds=body.cooldown_seconds,
    ))
    cfg = _read_config_json()
    cfg["circuit_breaker"] = {
        "time_window_seconds": body.time_window_seconds,
        "failure_rate_threshold": body.failure_rate_threshold,
        "cooldown_seconds": body.cooldown_seconds,
    }
    _write_config_json(cfg)
    return {"ok": True, "config": cb.get_config_dict()}


# ── 模型测试连通 ──────────────────────────────────────


class ModelTestBody(BaseModel):
    """测试指定端点配置；优先使用请求体（表单未保存时也能测）。"""

    id: Optional[str] = Field(None, description="端点 id（可选）")
    name: str = Field(..., description="Provider 模型名")
    base_url: Optional[str] = None
    api_key: Optional[str] = ""
    priority: Optional[int] = None


@router.post("/test")
def test_model_connectivity(body: ModelTestBody) -> Dict[str, Any]:
    """测试模型连通性：按请求体中的 base_url / api_key 发起探测。"""
    model_cfg: Dict[str, Any] = {
        "id": body.id,
        "name": body.name,
        "base_url": body.base_url,
        "api_key": body.api_key or "",
        "priority": body.priority,
    }
    if not str(body.name or "").strip():
        return {"ok": False, "error": "模型名称不能为空"}
    return _run_connectivity_test(model_cfg)


@router.post("/test/{model_name:path}")
def test_model_connectivity_legacy(model_name: str) -> Dict[str, Any]:
    """兼容旧接口：仅按名称匹配时只测试配置列表中的第一条同名记录。"""
    for idx, m in enumerate(_configured_models()):
        if str(m.get("name", "")) == model_name:
            return _run_connectivity_test(m)
    if model_name == settings.vlm_model_name:
        return _run_connectivity_test({
            "name": model_name,
            "base_url": None,
            "api_key": settings.vlm_api_key,
        })
    return {"ok": False, "error": f"Model '{model_name}' not found in configuration"}


# ── 熔断重置 ──────────────────────────────────────────


@router.post("/reset")
def reset_all_circuit_breakers() -> Dict[str, Any]:
    """重置所有端点的熔断状态。"""
    cb = get_circuit_breaker()
    cb.reset_all()
    return {"ok": True}


@router.post("/reset/{endpoint_id:path}")
def reset_model_circuit_breaker(endpoint_id: str) -> Dict[str, Any]:
    """重置指定端点的熔断状态。"""
    cb = get_circuit_breaker()
    cb.reset(endpoint_id)
    return {"ok": True, "endpoint_id": endpoint_id}
