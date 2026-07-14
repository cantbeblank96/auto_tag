"""VLM / 建簇时序采集（pipeline_debug 开启时启用，结束由 vlm_timing_report 出图）。"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_t0: Optional[float] = None
_events: List[Dict[str, Any]] = []
_meta: Dict[str, Any] = {}
_enabled = False


def is_enabled() -> bool:
    return _enabled


def configure(*, enabled: bool, meta: Optional[Dict[str, Any]] = None) -> None:
    global _enabled, _t0, _events, _meta
    with _lock:
        _enabled = bool(enabled)
        _t0 = time.perf_counter() if _enabled else None
        _events = []
        _meta = dict(meta or {})


def _now() -> float:
    if _t0 is None:
        return 0.0
    return round(time.perf_counter() - _t0, 4)


def record(event: str, **fields: Any) -> None:
    if not _enabled:
        return
    row = {"t": _now(), "event": event, **fields}
    with _lock:
        _events.append(row)


def save_json(path: str) -> None:
    if not _enabled:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _lock:
        payload = {
            "meta": dict(_meta),
            "events": list(_events),
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def resolve_enabled(pipeline_debug: Optional[bool]) -> bool:
    """与 pipeline_profile.resolve_pipeline_debug 对齐；另支持 AUTO_TAG_VLM_TIMING。"""
    from auto_tag.core.pipeline_profile import resolve_pipeline_debug

    if resolve_pipeline_debug(pipeline_debug):
        return True
    env = os.environ.get("AUTO_TAG_VLM_TIMING", "").strip().lower()
    return env in ("1", "true", "yes", "on")
