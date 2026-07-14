#!/usr/bin/env python3
"""复现 1970 长尾任务的 VLM HTTP 往来，保存 request/response/error 到目录。"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from auto_tag.core.circuit_breaker import get_circuit_breaker  # noqa: E402
from auto_tag.core.config import settings  # noqa: E402
from auto_tag.core.image_load_context import ImageLoadContext  # noqa: E402
from auto_tag.core.vlm_client import (  # noqa: E402
    VLMClient,
    _build_chat_url,
    _extract_content,
    openai_chat_completion,
)

TARGET_BASENAME = "1970.02.25-10.04.55.263.jpg"
DEFAULT_OUT = REPO / "auto_tag" / "work_dir" / "log" / "vlm_http_capture_1970"

_capture_lock = threading.Lock()
_capture_state: Dict[str, Any] = {
    "out_dir": None,
    "attempt": 0,
    "target_thread": None,
    "records": [],
}


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = dict(headers)
    auth = out.get("Authorization", "")
    if auth.startswith("Bearer "):
        out["Authorization"] = "Bearer ***REDACTED***"
    return out


def _serialize_messages(messages: List[Dict[str, Any]], attempt: int, out_dir: Path) -> List[Any]:
    """保存 prompt 文本；图片另存 jpg，JSON 里只留引用。"""
    serialized: List[Any] = []
    for mi, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            serialized.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            serialized.append(msg)
            continue
        parts: List[Any] = []
        for pi, part in enumerate(content):
            if part.get("type") == "text":
                parts.append(part)
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url") or ""
                img_path = out_dir / f"attempt_{attempt:02d}_msg{mi}_part{pi}.jpg"
                if url.startswith("data:image/jpeg;base64,"):
                    import base64

                    raw = base64.b64decode(url.split(",", 1)[1])
                    img_path.write_bytes(raw)
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"file://{img_path.name}",
                                "base64_chars": len(url),
                                "jpeg_bytes": len(raw),
                            },
                        }
                    )
                else:
                    parts.append(part)
            else:
                parts.append(part)
        serialized.append({"role": role, "content": parts})
    return serialized


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _capturing_openai_chat_completion(
    model: str,
    messages: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """仅对目标线程的 HTTP 调用落盘 request/response/error。"""
    thread_name = threading.current_thread().name
    out_dir: Path = _capture_state["out_dir"]
    is_target = thread_name == _capture_state.get("target_thread")

    url = _build_chat_url(base_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if response_format:
        body["response_format"] = response_format

    attempt = 0
    if is_target:
        with _capture_lock:
            _capture_state["attempt"] += 1
            attempt = _capture_state["attempt"]

        req_doc = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "thread": thread_name,
            "attempt": attempt,
            "method": "POST",
            "url": url,
            "headers": _redact_headers(headers),
            "body": {
                "model": model,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "messages": _serialize_messages(messages, attempt, out_dir),
            },
            "timeout_s": timeout,
        }
        _write_json(out_dir / f"attempt_{attempt:02d}_request.json", req_doc)

    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.post(url, headers=headers, json=body)
            elapsed = round(time.perf_counter() - t0, 3)
            if is_target:
                resp_doc = {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "thread": thread_name,
                    "attempt": attempt,
                    "elapsed_s": elapsed,
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "body_text": resp.text,
                }
                try:
                    resp_doc["body_json"] = resp.json()
                    resp_doc["extracted_content"] = _extract_content(resp_doc["body_json"])
                except Exception as e:
                    resp_doc["json_parse_error"] = str(e)
                _write_json(out_dir / f"attempt_{attempt:02d}_response.json", resp_doc)
                with _capture_lock:
                    _capture_state["records"].append(
                        {
                            "attempt": attempt,
                            "outcome": "http_response",
                            "elapsed_s": elapsed,
                            "status_code": resp.status_code,
                            "resp_chars": len(resp_doc.get("extracted_content") or ""),
                        }
                    )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        elapsed = round(time.perf_counter() - t0, 3)
        if is_target:
            err_doc = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "thread": thread_name,
                "attempt": attempt,
                "elapsed_s": elapsed,
                "error_type": type(e).__name__,
                "error": str(e),
                "note": (
                    "读超时 = httpx 在 timeout 内未收到完整 HTTP 响应；"
                    "无 response body 可保存，只有本次 request + 此 error。"
                ),
            }
            _write_json(out_dir / f"attempt_{attempt:02d}_error.json", err_doc)
            with _capture_lock:
                _capture_state["records"].append(
                    {
                        "attempt": attempt,
                        "outcome": "http_failed",
                        "elapsed_s": elapsed,
                        "error_type": type(e).__name__,
                    }
                )
        raise


def _find_target_image() -> Path:
    root = Path("/home/SENSETIME/xukaiming/Desktop/temp/little_data")
    matches = list(root.rglob(TARGET_BASENAME))
    if not matches:
        raise FileNotFoundError(TARGET_BASENAME)
    return matches[0]


def _filler_paths() -> List[Path]:
    root = Path("/home/SENSETIME/xukaiming/Desktop/temp/little_data")
    names = [
        "20090106-00-20-57_rgb.jpg",
        "2025.02.19-11.15.43.952.jpg",
        "20090102-20-50-38_rgb.jpg",
        "19700217-01-04-53_rgb.jpg",
        "20090106-19-09-55_rgb.jpg",
        "20090101-20-12-58_rgb.jpg",
        "19700213-22-37-07_rgb.jpg",
        "19700215-02-20-16_rgb.jpg",
        "2018.06.08-16.22.43.054.jpg",
    ]
    return [next(root.rglob(n)) for n in names]


def run_capture(*, out_dir: Path, concurrent: int = 10, timeout: float = 120.0) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    _capture_state["out_dir"] = out_dir
    _capture_state["attempt"] = 0
    _capture_state["records"] = []
    _capture_state["target_thread"] = "capture_target"

    ctx = ImageLoadContext(
        mixed_yuv=True,
        yuv_type="nv21",
        image_width=640,
        image_height=480,
        rotate_angle="ROTATE_90_COUNTERCLOCKWISE",
    )
    target_path = _find_target_image()
    target_img = ctx.load_path(str(target_path), None)
    fillers = [(p, ctx.load_path(str(p), None)) for p in _filler_paths()]

    # 保存源图副本
    target_img.save(out_dir / "source_image_pipeline.jpg", format="JPEG")

    import auto_tag.core.vlm_client as vc

    original_fn = vc.openai_chat_completion

    def _wrapped(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return _capturing_openai_chat_completion(*args, **kwargs)

    vc.openai_chat_completion = _wrapped

    thread_local = threading.local()

    def get_client() -> VLMClient:
        if not hasattr(thread_local, "client"):
            thread_local.client = VLMClient(
                models=settings.vlm_models,
                circuit_breaker=get_circuit_breaker(),
            )
        return thread_local.client

    def annotate_labeled(img: Image.Image, label: str) -> Dict[str, Any]:
        if label == "target":
            threading.current_thread().name = "capture_target"
        t0 = time.perf_counter()
        result = get_client().annotate_image(img)
        return {
            "label": label,
            "total_s": round(time.perf_counter() - t0, 1),
            "valid": VLMClient.validate_against_questions(result)["valid"],
            "result": result,
        }

    meta = {
        "target_image": str(target_path),
        "concurrent": concurrent,
        "model": settings.vlm_models[0]["name"] if settings.vlm_models else None,
        "timeout_s": timeout,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "note": "10 路并发复现 benchmark 条件；仅 capture_target 线程的 HTTP 落盘",
    }
    _write_json(out_dir / "meta.json", meta)

    wall_t0 = time.time()
    results: List[Dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=concurrent) as ex:
            futs = [
                ex.submit(annotate_labeled, img, f"filler_{i}")
                for i, (_, img) in enumerate(fillers)
            ]
            futs.append(ex.submit(annotate_labeled, target_img, "target"))
            for fut in futs:
                results.append(fut.result())
    finally:
        vc.openai_chat_completion = original_fn

    meta["finished_utc"] = datetime.now(timezone.utc).isoformat()
    meta["wall_s"] = round(time.time() - wall_t0, 1)
    meta["http_records"] = _capture_state["records"]
    meta["annotate_results"] = [
        {k: v for k, v in r.items() if k != "result"} for r in results
    ]
    target_r = next((r for r in results if r["label"] == "target"), None)
    if target_r:
        _write_json(out_dir / "attempt_final_labels.json", target_r.get("result") or {})
    _write_json(out_dir / "meta.json", meta)

    summary_lines = [
        f"# VLM HTTP capture: {TARGET_BASENAME}",
        "",
        f"- out_dir: `{out_dir}`",
        f"- wall: {meta['wall_s']}s",
        f"- target annotate: {target_r['total_s'] if target_r else '?'}s",
        "",
        "## HTTP attempts (target thread only)",
        "",
    ]
    for rec in _capture_state["records"]:
        if rec["outcome"] == "http_response":
            summary_lines.append(
                f"- attempt {rec['attempt']:02d}: HTTP {rec['status_code']} "
                f"elapsed={rec['elapsed_s']}s resp_chars={rec['resp_chars']}"
            )
        else:
            summary_lines.append(
                f"- attempt {rec['attempt']:02d}: {rec['error_type']} "
                f"elapsed={rec['elapsed_s']}s → 见 attempt_{rec['attempt']:02d}_error.json"
            )
    summary_lines.extend(
        [
            "",
            "## 文件说明",
            "- `attempt_XX_request.json`: 发出的 HTTP 请求（prompt + 图片引用）",
            "- `attempt_XX_response.json`: 成功时的完整 HTTP 响应",
            "- `attempt_XX_error.json`: 超时/网络错误（无 response body）",
            "- `attempt_XX_msg0_part1.jpg`: 请求里附带的 JPEG",
        ]
    )
    (out_dir / "README.md").write_text("\n".join(summary_lines), encoding="utf-8")
    return meta


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--concurrent", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=120.0, help="httpx 读超时秒数")
    args = parser.parse_args()
    meta = run_capture(
        out_dir=Path(args.out_dir), concurrent=args.concurrent, timeout=args.timeout
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"\nSaved to: {args.out_dir}")


if __name__ == "__main__":
    main()
