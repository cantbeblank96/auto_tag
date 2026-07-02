"""
models router: 管理 VLM 模型配置 + 熔断状态。

注意：模型配置持久化到 config.json，熔断状态存在于内存（circuit_breaker 单例）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from auto_tag.core.config import settings, _AUTO_TAG_DIR
from auto_tag.core.circuit_breaker import get_circuit_breaker, CircuitBreakerConfig

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


# ── 模型管理 ──────────────────────────────────────────


@router.get("")
def list_models() -> Dict[str, Any]:
    """获取当前配置的模型列表 + 各模型的熔断状态。"""
    cb = get_circuit_breaker()
    states = cb.get_all_states()
    # 从 settings 读取当前配置的模型列表
    raw_models = getattr(settings, "vlm_models", None) or []
    # 若 settings 的 models 为空，回退到单模型方式
    if not raw_models:
        raw_models = [
            {
                "name": settings.vlm_model_name,
                "base_url": None,
                "api_key": settings.vlm_api_key,
                "priority": 1,
            }
        ]
    models_list = []
    for m in raw_models:
        name = str(m.get("name", ""))
        st = states.get(name, {})
        models_list.append({
            "name": name,
            "base_url": m.get("base_url"),
            "api_key": m.get("api_key", ""),
            "priority": int(m.get("priority", 99)),
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
    """获取熔断配置 + 各模型实时状态。"""
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
    # 持久化到 config.json
    cfg = _read_config_json()
    cfg["circuit_breaker"] = {
        "time_window_seconds": body.time_window_seconds,
        "failure_rate_threshold": body.failure_rate_threshold,
        "cooldown_seconds": body.cooldown_seconds,
    }
    _write_config_json(cfg)
    return {"ok": True, "config": cb.get_config_dict()}


# ── 模型测试连通 ──────────────────────────────────────


@router.post("/test/{model_name:path}")
def test_model_connectivity(model_name: str) -> Dict[str, Any]:
    """测试模型连通性：发送一条简单文本请求到 OpenAI 兼容接口。"""
    import time
    import httpx

    # 查找模型配置
    raw_models = getattr(settings, "vlm_models", None) or []
    model_cfg = None
    for m in raw_models:
        if str(m.get("name", "")) == model_name:
            model_cfg = m
            break
    if not model_cfg:
        if model_name == settings.vlm_model_name:
            model_cfg = {"name": model_name, "base_url": None, "api_key": settings.vlm_api_key}
    if not model_cfg:
        return {"ok": False, "error": f"Model '{model_name}' not found in configuration"}

    base_url = model_cfg.get("base_url") or ""
    api_key = model_cfg.get("api_key") or ""

    # 构造请求 URL
    url = "https://api.openai.com/v1/chat/completions"
    if base_url:
        url = base_url.rstrip("/")
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
        return {"ok": True, "latency_ms": elapsed, "response": content, "model": model_name}
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        return {"ok": False, "latency_ms": elapsed, "error": str(e)[:300], "model": model_name}


# ── 熔断重置 ──────────────────────────────────────────


@router.post("/reset")
def reset_all_circuit_breakers() -> Dict[str, Any]:
    """重置所有模型的熔断状态。"""
    cb = get_circuit_breaker()
    cb.reset_all()
    return {"ok": True}


@router.post("/reset/{model_name:path}")
def reset_model_circuit_breaker(model_name: str) -> Dict[str, Any]:
    """重置指定模型的熔断状态。"""
    cb = get_circuit_breaker()
    cb.reset(model_name)
    return {"ok": True, "model": model_name}