#!/usr/bin/env python3
"""用假的商汤接口验证开工坡度和限流等待，不访问真实 API。

用法（仓库根目录）：
    uv run python tests/verify_vlm_pace.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_tag.core.vlm_annotation_pool import ConcurrencyRamp
from auto_tag.core.vlm_client import (
    VLMClient,
    VLMNoFailoverError,
    reset_vlm_post_pacer_for_tests,
)


MODEL = {
    "name": "sensenova-6.8-flash-lite",
    "base_url": "https://token.sensenova.cn/v1",
    "api_key": "test",
}
OK = {"choices": [{"message": {"content": '{"ok":1}'}, "finish_reason": "stop"}]}


def _check(name: str, ok: bool, detail: str) -> bool:
    mark = "通过" if ok else "失败"
    print(f"[{mark}] {name}: {detail}")
    return ok


def verify_ramp() -> bool:
    """8 个任务、上限 4。第一张没完成前只能有 1 路在途，最终不超过 4。"""
    ramp = ConcurrencyRamp(4)
    lock = threading.Lock()
    state = {"active": 0, "max": 0, "first_wave": 0, "releases": 0}

    def worker() -> None:
        ramp.acquire()
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            if state["releases"] == 0:
                state["first_wave"] = max(state["first_wave"], state["active"])
        time.sleep(0.08)
        with lock:
            state["active"] -= 1
            state["releases"] += 1
        ramp.release(True)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ok = state["first_wave"] == 1 and state["max"] <= 4 and ramp.limit == 4
    return _check(
        "开工坡度",
        ok,
        f"第一批同时在途 {state['first_wave']}，全程最多 {state['max']}，"
        f"成功后上限升到 {ramp.limit}/4",
    )


def _http_error(status: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://token.sensenova.cn/v1/chat/completions")
    response = httpx.Response(
        status,
        headers={"retry-after": "0.4"},
        request=request,
        text='{"error":{"message":"%s"}}' % message,
    )
    return httpx.HTTPStatusError(message, request=request, response=response)


def verify_interval_and_429() -> bool:
    """4 路同时请求。前两次返回 429，之后成功。两次 POST 必须隔开间隔。"""
    reset_vlm_post_pacer_for_tests()
    posts: list[tuple[float, int]] = []
    lock = threading.Lock()
    started = time.monotonic()

    def fake(**_kwargs):
        with lock:
            n = len(posts) + 1
            posts.append((time.monotonic() - started, n))
            status = 429 if n == 1 else 200
        if status == 429:
            raise _http_error(429, "inference exceeds tpm/rpm limit")
        return OK

    client = VLMClient.__new__(VLMClient)
    errors: list[BaseException] = []

    def one() -> None:
        try:
            client._chat_raw(MODEL, [{"role": "user", "content": "ping"}])
        except BaseException as exc:
            errors.append(exc)

    from unittest.mock import patch

    with patch("auto_tag.core.vlm_client.openai_chat_completion", fake), \
            patch("auto_tag.core.vlm_client._pace_settings", return_value=(0.2, 0.4, 0.3)), \
            patch("auto_tag.core.vlm_client.resolve_max_tokens_for_model", return_value=16):
        threads = [threading.Thread(target=one) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    gaps = [
        round(posts[i][0] - posts[i - 1][0], 3) for i in range(1, len(posts))
    ]
    cooldown_ok = len(posts) >= 2 and posts[1][0] - posts[0][0] >= 0.35
    spaced = all(gap >= 0.18 for gap in gaps)
    ok = not errors and len(posts) == 5 and spaced and cooldown_ok
    return _check(
        "商汤间隔和 429 冷却",
        ok,
        f"{len(posts)} 次 POST，间隔 {gaps} 秒，异常 {len(errors)}。"
        "4 张图只在第一次遇到 429，冷却后再继续，总共多 1 次重试",
    )


def verify_429_does_not_storm() -> bool:
    """429 一直不恢复时，同一张图只打 2 次，不再连打 4 次或换端点。"""
    reset_vlm_post_pacer_for_tests()
    calls = {"n": 0}

    def always_429(**_kwargs):
        calls["n"] += 1
        raise _http_error(429, "rpm exhausted")

    client = VLMClient.__new__(VLMClient)
    from unittest.mock import patch

    with patch("auto_tag.core.vlm_client.openai_chat_completion", always_429), \
            patch("auto_tag.core.vlm_client._pace_settings", return_value=(0.0, 0.05, 0.05)), \
            patch("auto_tag.core.vlm_client.resolve_max_tokens_for_model", return_value=16):
        try:
            client._chat_raw(MODEL, [])
            raised = None
        except VLMNoFailoverError as exc:
            raised = exc
    ok = raised is not None and calls["n"] == 2
    return _check(
        "429 不再放大重试",
        ok,
        f"持续 429 时实际 POST {calls['n']} 次（期望 2），"
        f"结果 {type(raised).__name__ if raised else '没有抛出'}",
    )


def main() -> int:
    print("验证开工坡度和商汤限流（假接口，不访问外网）")
    results = [
        verify_ramp(),
        verify_interval_and_429(),
        verify_429_does_not_storm(),
    ]
    if all(results):
        print("三项都符合当前策略。")
        return 0
    print("有检查没有通过。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
