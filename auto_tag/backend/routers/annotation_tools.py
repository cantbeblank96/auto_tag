"""annotation_tools router：标注工具注册表查询 + VLM 智能分析建议。

- GET  /api/annotation_tools：工具注册表 + 各工具 enabled/available/原因
- POST /api/annotation_tools/analyze：把问题定义与工具描述交给 VLM，
  返回每个问题建议绑定的工具映射（人工可在设置页修改）
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auto_tag.core.annotation_tools import (
    TOOL_REGISTRY,
    list_tool_status,
)
from auto_tag.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/annotation_tools", tags=["annotation_tools"])


@router.get("")
def list_annotation_tools() -> Dict[str, Any]:
    """工具注册表与当前状态（供设置页「工具管理」与 Questions 候选）。"""
    tools_cfg = getattr(settings, "annotation_tools", None) or {}
    align_paths = tools_cfg.get("align_model_paths") if isinstance(tools_cfg, dict) else []
    return {
        "tools": list_tool_status(),
        "align_model_paths": list(align_paths or []),
    }


class AnalyzeBody(BaseModel):
    questions: Dict[str, Any] = Field(..., description="当前问题定义（questions 段）")


def _pick_model_for_analysis() -> Dict[str, Any]:
    models = getattr(settings, "vlm_models", None) or []
    for m in models:
        if isinstance(m, dict) and m.get("enabled", True):
            return m
    raise HTTPException(503, "未配置可用的 VLM 模型（vlm_models），无法执行智能分析")


def _extract_json_object(content: str) -> Dict[str, Any]:
    text = (content or "").strip()
    # 去掉 markdown fence 与首尾非 JSON 内容
    if "```" in text:
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise HTTPException(422, f"VLM 分析结果无法解析为 JSON: {content[:200]}")
    return json.loads(text[start : end + 1])


@router.post("/analyze")
def analyze_tools_for_questions(body: AnalyzeBody) -> Dict[str, Any]:
    """由 VLM 分析问题定义，建议每个问题应绑定的标注工具。"""
    from auto_tag.core.vlm_client import openai_chat_completion

    questions = body.questions or {}
    if not questions:
        return {"suggestions": {}, "raw": ""}

    valid_names = {t["name"] for t in TOOL_REGISTRY}
    tools_desc = [
        {"name": t["name"], "display_name": t["display_name"], "description": t["description"]}
        for t in TOOL_REGISTRY
    ]
    # 精简问题定义：只保留语义相关字段，降低 token 消耗
    slim_questions: Dict[str, Any] = {}
    for k, d in questions.items():
        if not isinstance(d, dict):
            continue
        slim_questions[k] = {
            kk: d[kk]
            for kk in ("type", "description", "choices", "min", "max")
            if kk in d
        }

    prompt = f"""You are the configuration assistant of an image annotation system.

Annotation question definitions (JSON):
{json.dumps(slim_questions, indent=2, ensure_ascii=False)}

Available annotation tools (JSON, each provides objective measurements injected into the VLM prompt):
{json.dumps(tools_desc, indent=2, ensure_ascii=False)}

Task: for each question, decide which tools would provide objective information that helps
answer it better (e.g. a question about face size benefits from face_detect).
Return ONLY a JSON object mapping question key to a list of tool names, e.g.
{{"face_size": ["face_detect"]}}. Use an empty list for questions that need no tools.
Only use tool names from the list above. Do not include any other text."""

    model = _pick_model_for_analysis()
    try:
        resp = openai_chat_completion(
            model=str(model.get("name") or ""),
            messages=[{"role": "user", "content": prompt}],
            api_key=model.get("api_key"),
            base_url=model.get("base_url"),
            timeout=60,
            # thinking 模型的 reasoning 与 content 共用预算，给足余量防截断
            max_tokens=16384,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("annotation_tools analyze 调用 VLM 失败: %s", e)
        raise HTTPException(502, f"调用 VLM 失败: {str(e)[:200]}")

    try:
        content = resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise HTTPException(422, "VLM 返回结构异常")

    try:
        parsed = _extract_json_object(content)
    except json.JSONDecodeError:
        raise HTTPException(422, f"VLM 返回非法 JSON: {content[:200]}")

    # 校验：仅保留已知问题 key 与已注册工具名
    suggestions: Dict[str, Any] = {}
    for k, tools in parsed.items():
        if k not in slim_questions:
            continue
        if isinstance(tools, str):
            tools = [tools]
        if not isinstance(tools, list):
            continue
        kept = [t for t in tools if isinstance(t, str) and t in valid_names]
        suggestions[k] = sorted(set(kept))
    return {"suggestions": suggestions, "raw": content}
