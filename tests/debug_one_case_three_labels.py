#!/usr/bin/env python3
"""对一张坏例做带参考图的真实标注，并落盘每个阶段的输入与模型原文。

只标注 data/sample_image 里有参考图的三个问题：
head_pose、face_distance、face_size。每个问题单独请求，参考图互不混入。

配置里的 D:\\dev\\sample_image 在本机不存在。本脚本只在进程内把这三个问题的
examples 改写到 data/sample_image，不修改 config.json。

用法（仓库根目录）:
    uv run python tests/debug_one_case_three_labels.py
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASE_REL = (
    "data/10.29 bad case/head_pose/slight_pitch-frontal/"
    "0_0_2025.06.04-14.41.25.302.jpg"
)
SAMPLE_ROOT = ROOT / "data" / "sample_image"
KEYS = ("head_pose", "face_distance", "face_size")
# 生产配置是 60s。7 张参考图加工具测量容易顶满，这里放宽只为拿到模型原文，
# 并在日志里同时写下生产超时和本次实际耗时。
DEBUG_HTTP_TIMEOUT = 180.0
RUN_DIR = ROOT / "debug_from_cursor" / "one_case_0_0_2025.06.04-14.41.25.302"


def remap_sample_path(raw: str) -> str:
    """把配置中的 Windows 样图根目录改到本机 data/sample_image。"""
    text = str(raw or "").strip().replace("\\", "/")
    marker = "D:/dev/sample_image"
    if text.lower().startswith(marker.lower()):
        rest = text[len(marker) :].lstrip("/")
        return str(SAMPLE_ROOT / rest)
    return str(raw or "").strip()


def apply_local_examples(questions: dict) -> dict:
    """返回改写前后的路径对照。就地修改 settings.questions。"""
    mapping: dict[str, list[dict[str, str]]] = {}
    for key in KEYS:
        details = questions.get(key)
        if not isinstance(details, dict):
            raise SystemExit(f"questions 中缺少 {key}")
        examples = details.get("examples")
        if not isinstance(examples, dict):
            raise SystemExit(f"{key} 没有 examples")
        rows = []
        rewritten = {}
        for value, raw in examples.items():
            local = remap_sample_path(raw)
            rewritten[value] = local
            rows.append({"value": str(value), "config_path": str(raw), "local_path": local})
        examples.clear()
        examples.update(rewritten)
        mapping[key] = rows
    return mapping


def selected_examples(key: str) -> list[dict[str, Any]]:
    """与标注时相同的规则：每个档位最多 1 张，文件名 casefold 字典序。"""
    from auto_tag.core.config import settings
    from auto_tag.core.example_sources import (
        list_images_in_directory,
        resolve_example_path,
        take_example_images,
    )

    details = settings.questions[key]
    rows = []
    for value, raw in details.get("examples", {}).items():
        resolved = resolve_example_path(raw)
        exists = os.path.exists(resolved)
        chosen, truncated = take_example_images([raw] if raw else [])
        directory_files = []
        if os.path.isdir(resolved):
            directory_files = [os.path.basename(p) for p in list_images_in_directory(resolved)]
        rows.append(
            {
                "value": str(value),
                "resolved": resolved,
                "exists": exists,
                "chosen": chosen,
                "truncated": truncated,
                "directory_files": directory_files,
            }
        )
    rows.sort(key=lambda r: r["value"])
    return rows


def materialize_message(content: Any, img_dir: Path, prefix: str) -> Any:
    """去掉 base64，把实际送出的 JPEG 存盘。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content
    img_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, part in enumerate(content):
        if not isinstance(part, dict):
            parts.append(part)
            continue
        if part.get("type") == "text":
            parts.append({"type": "text", "text": part.get("text") or ""})
            continue
        if part.get("type") != "image_url":
            parts.append({"type": part.get("type"), "note": "unhandled part"})
            continue
        url = ((part.get("image_url") or {}).get("url")) or ""
        b64 = url.split(",", 1)[1] if "," in url else url
        raw = base64.b64decode(b64)
        name = f"{prefix}_t{i:02d}.jpg"
        path = img_dir / name
        path.write_bytes(raw)
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            size = list(im.size)
        parts.append(
            {
                "type": "image",
                "file": str(path.relative_to(ROOT)),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "size": size,
            }
        )
    return parts


def install_trace(traces: list[dict[str, Any]]) -> None:
    from auto_tag.core.vlm_client import VLMClient

    original = VLMClient._chat_raw

    def wrapped(self, model, messages, profile=None):
        bucket = traces[-1]
        turn = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "model": model.get("name"),
            "message_count": len(messages),
        }
        t0 = time.perf_counter()
        try:
            content, finish_reason = original(self, model, messages, profile=profile)
        except Exception as exc:
            turn["elapsed_s"] = round(time.perf_counter() - t0, 3)
            turn["error_type"] = type(exc).__name__
            turn["error"] = str(exc)[:2000]
            turn["messages"] = [
                {
                    "role": m.get("role"),
                    "content": materialize_message(
                        m.get("content"),
                        RUN_DIR / "sent_images" / bucket["key"],
                        f"turn{len(bucket['turns'])}",
                    ),
                }
                for m in messages
            ]
            bucket["turns"].append(turn)
            raise
        turn["elapsed_s"] = round(time.perf_counter() - t0, 3)
        turn["finish_reason"] = finish_reason
        turn["raw_content"] = content
        turn["messages"] = [
            {
                "role": m.get("role"),
                "content": materialize_message(
                    m.get("content"),
                    RUN_DIR / "sent_images" / bucket["key"],
                    f"turn{len(bucket['turns'])}",
                ),
            }
            for m in messages
        ]
        bucket["turns"].append(turn)
        return content, finish_reason

    VLMClient._chat_raw = wrapped


def create_vlm():
    from auto_tag.core.circuit_breaker import CircuitBreakerConfig, get_circuit_breaker
    from auto_tag.core.config import settings
    from auto_tag.core.vlm_client import VLMClient

    models = getattr(settings, "vlm_models", None) or []
    if models:
        cb = get_circuit_breaker()
        cb.update_config(
            CircuitBreakerConfig(
                time_window_seconds=settings.circuit_breaker_time_window,
                failure_rate_threshold=settings.circuit_breaker_failure_threshold,
                cooldown_seconds=settings.circuit_breaker_cooldown,
            )
        )
        return VLMClient(models=models, circuit_breaker=cb)
    return VLMClient(
        model_name=settings.vlm_model_name,
        api_key=settings.vlm_api_key,
    )


def annotate_one(vlm, image, key: str) -> dict[str, Any]:
    from auto_tag.core.config import settings

    existing = {k: None for k in settings.questions if k != key}
    t0 = time.perf_counter()
    try:
        result = vlm.annotate_image_incremental(image, existing)
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_s": round(time.perf_counter() - t0, 3),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    value = result.get(key) if isinstance(result, dict) else None
    return {
        "ok": True,
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "returned_value": value,
        "returned_type": type(value).__name__,
        "full_return_keys": sorted(result.keys()) if isinstance(result, dict) else [],
    }


def _md_code(text: str, lang: str = "") -> str:
    fence = "```"
    return f"{fence}{lang}\n{text}\n{fence}\n"


def write_markdown(payload: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# 单图三标签真实标注记录")
    lines.append("")
    lines.append(f"- 时间: {payload['started_at']}")
    lines.append(f"- 图片: `{payload['case']}`")
    lines.append(
        f"- 送入流程的尺寸: {payload['image_size']}，模式 {payload['image_mode']}"
    )
    if payload.get("target_rotate_ccw"):
        lines.append(f"- 待标注图逆时针旋转: {payload['target_rotate_ccw']} 度")
    lines.append(f"- 模型: {payload['model_name']}")
    lines.append(f"- 生产 HTTP 超时: {payload['production_timeout_s']} 秒")
    lines.append(f"- 本次调试超时: {payload['debug_timeout_s']} 秒")
    lines.append(f"- max_tokens: {payload['max_tokens']}")
    if payload.get("max_tokens_override"):
        lines.append(
            f"- max_tokens 覆盖: {payload['max_tokens_override']}（生产路径会向模型查询上限，本次为调试覆盖）"
        )
    lines.append(f"- 校验追问上限: {payload['max_corrections']}")
    lines.append("")
    lines.append(
        "每个问题单独请求。参考图只包含该问题自己的 examples。"
        "模型被提示词要求只返回 JSON，因此「决策」以每一轮原文为准，不补写推理过程。"
    )
    lines.append("")
    for item in payload["labels"]:
        lines.append(f"## {item['key']}")
        lines.append("")
        lines.append(f"- 绑定工具: {item['tools']}")
        lines.append(f"- 调用结果: {'成功' if item['call']['ok'] else '失败'}")
        lines.append(f"- 端到端耗时: {item['call']['elapsed_s']} 秒")
        if item["call"].get("ok"):
            lines.append(
                f"- 最终值: `{item['call']['returned_value']}`"
                f"（{item['call']['returned_type']}）"
            )
        else:
            lines.append(f"- 错误: `{item['call'].get('error_type')}` {item['call'].get('error')}")
        lines.append("")
        lines.append("### 参考图选取")
        lines.append("")
        lines.append("| 档位 | 实际选用 | 同目录其余文件 | 路径存在 |")
        lines.append("| --- | --- | --- | --- |")
        for row in item["examples"]:
            chosen = ", ".join(Path(p).name for p in row["chosen"]) or "(无)"
            rest = [
                name
                for name in row["directory_files"]
                if name not in {Path(p).name for p in row["chosen"]}
            ]
            lines.append(
                f"| `{row['value']}` | `{chosen}` | {', '.join(rest) or '—'} | {row['exists']} |"
            )
        lines.append("")
        for turn_i, turn in enumerate(item["turns"]):
            lines.append(f"### 第 {turn_i} 轮")
            lines.append("")
            lines.append(
                f"- 耗时: {turn.get('elapsed_s')} 秒"
                f"；finish_reason: `{turn.get('finish_reason')}`"
            )
            if turn.get("error"):
                lines.append(f"- HTTP 错误: `{turn.get('error_type')}` {turn.get('error')}")
            lines.append("")
            for msg_i, message in enumerate(turn.get("messages") or []):
                content = message.get("content")
                lines.append(f"消息 {msg_i} role=`{message.get('role')}`")
                lines.append("")
                if isinstance(content, str):
                    lines.append(_md_code(content, "text"))
                    continue
                if not isinstance(content, list):
                    continue
                lines.append("块顺序：")
                lines.append("")
                for part in content:
                    if part.get("type") == "text":
                        text = part.get("text") or ""
                        head = text.splitlines()[0][:120] if text else ""
                        lines.append(f"- 文本（{len(text)} 字）: {head}")
                    elif part.get("type") == "image":
                        lines.append(
                            f"- 图片 `{part['file']}` {part['size']} {part['bytes']} bytes"
                        )
                lines.append("")
                for part in content:
                    if part.get("type") == "text":
                        lines.append(_md_code(part.get("text") or "", "text"))
            if "raw_content" in turn:
                lines.append("模型原文：")
                lines.append("")
                lines.append(_md_code(turn.get("raw_content") or "", "json"))
        if not item["call"].get("ok") and item["call"].get("traceback"):
            lines.append("异常栈：")
            lines.append("")
            lines.append(_md_code(item["call"]["traceback"], "text"))
        lines.append("")
    path = RUN_DIR / "log.md"
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keys",
        nargs="+",
        default=list(KEYS),
        choices=list(KEYS),
        help="只跑指定问题。配合 --merge 时保留日志里其余问题的记录。",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="把本次结果写回已有 log.json，被替换的旧尝试留在 history。",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=0,
        help="覆盖 vlm_max_tokens。0 表示沿用配置（当前会自动解析成模型上限）。",
    )
    parser.add_argument(
        "--rotate-ccw",
        type=int,
        default=0,
        help="待标注图逆时针旋转的角度。0 表示不旋转。",
    )
    parser.add_argument(
        "--run-dir",
        default="",
        help="日志目录。空则使用默认的 one_case 目录。",
    )
    return parser.parse_args()


def main() -> None:
    global RUN_DIR
    args = parse_args()
    if args.run_dir:
        RUN_DIR = Path(args.run_dir)
        if not RUN_DIR.is_absolute():
            RUN_DIR = ROOT / RUN_DIR
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_file = RUN_DIR / "runtime.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    from auto_tag.core.config import settings
    from auto_tag.core.utils.load_image import load_image
    from auto_tag.core.vlm_client import resolve_max_tokens_for_model

    case = ROOT / CASE_REL
    if not case.is_file():
        raise SystemExit(f"待标注图不存在: {case}")

    production_timeout = float(settings.vlm_http_timeout)
    settings.vlm_http_timeout = DEBUG_HTTP_TIMEOUT
    if args.max_tokens > 0:
        settings.vlm_max_tokens = args.max_tokens
    path_mapping = apply_local_examples(settings.questions)

    image = load_image(str(case))
    if args.rotate_ccw % 360:
        image = image.rotate(args.rotate_ccw, expand=True)
        rotated_path = RUN_DIR / "target_rotated.jpg"
        image.save(rotated_path, quality=95)
        logging.info(
            "待标注图逆时针旋转 %s 度，尺寸 %s，已保存 %s",
            args.rotate_ccw,
            image.size,
            rotated_path,
        )
    vlm = create_vlm()
    model = vlm.models[0] if vlm.models else {}
    traces: list[dict[str, Any]] = []
    install_trace(traces)

    previous: dict[str, dict[str, Any]] = {}
    if args.merge and (RUN_DIR / "log.json").is_file():
        old = json.loads((RUN_DIR / "log.json").read_text(encoding="utf-8"))
        for item in old.get("labels") or []:
            previous[item["key"]] = item

    payload: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "case": str(case),
        "image_size": list(image.size),
        "image_mode": image.mode,
        "target_rotate_ccw": int(args.rotate_ccw or 0),
        "model_name": model.get("name"),
        "base_url": model.get("base_url"),
        "production_timeout_s": production_timeout,
        "debug_timeout_s": DEBUG_HTTP_TIMEOUT,
        "max_tokens": resolve_max_tokens_for_model(model) if model else None,
        "max_tokens_override": args.max_tokens or None,
        "max_corrections": int(settings.vlm_validation_max_corrections),
        "path_mapping": path_mapping,
        "labels": [],
    }

    fresh: dict[str, dict[str, Any]] = {}
    for key in args.keys:
        bucket = {"key": key, "turns": []}
        traces.append(bucket)
        examples = selected_examples(key)
        logging.info("开始标注 %s，参考档位 %d", key, len(examples))
        call = annotate_one(vlm, image, key)
        logging.info(
            "结束 %s ok=%s elapsed=%s value=%s",
            key,
            call.get("ok"),
            call.get("elapsed_s"),
            call.get("returned_value"),
        )
        details = settings.questions[key]
        item = {
            "key": key,
            "tools": list(details.get("tools") or []),
            "choices": list(details.get("choices") or []),
            "examples": examples,
            "turns": bucket["turns"],
            "call": call,
            "history": [],
        }
        old_item = previous.get(key)
        if old_item:
            item["history"] = list(old_item.get("history") or [])
            item["history"].append(
                {
                    "archived_at": payload["started_at"],
                    "call": old_item.get("call"),
                    "turns": old_item.get("turns"),
                }
            )
        fresh[key] = item

    ordered = []
    for key in KEYS:
        if key in fresh:
            ordered.append(fresh[key])
        elif key in previous:
            ordered.append(previous[key])
    payload["labels"] = ordered
    (RUN_DIR / "log.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown(payload)

    print(f"LOG {RUN_DIR / 'log.md'}")


if __name__ == "__main__":
    main()
