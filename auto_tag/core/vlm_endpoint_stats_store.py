"""VLM 端点调用统计持久化（熔断器内存态的补充，重启/CLI 任务后仍可在设置页展示）。"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from kevin_toolbox.data_flow.file import json_

from auto_tag.core.pipeline import work_log_dir

logger = logging.getLogger(__name__)

_STATS_FILENAME = "vlm_endpoint_stats.json"


def stats_file_path(work_dir: str) -> str:
    return os.path.join(work_log_dir(work_dir), _STATS_FILENAME)


def persist_circuit_breaker_states(work_dir: str) -> None:
    """将当前熔断器各端点统计写入 work_dir/log/vlm_endpoint_stats.json。"""
    from auto_tag.core.circuit_breaker import get_circuit_breaker

    wd = (work_dir or "").strip()
    if not wd:
        return
    payload = get_circuit_breaker().export_persistent_snapshot()
    if not payload.get("endpoints"):
        return
    path = stats_file_path(wd)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json_.write(content=payload, file_path=path, b_use_suggested_converter=True)
    logger.debug("Persisted VLM endpoint stats to %s", path)


def hydrate_circuit_breaker_from_disk(work_dir: Optional[str]) -> bool:
    """启动时从磁盘恢复端点统计；返回是否成功加载非空数据。"""
    from auto_tag.core.circuit_breaker import get_circuit_breaker

    wd = (work_dir or "").strip()
    if not wd:
        return False
    path = stats_file_path(wd)
    if not os.path.isfile(path):
        return False
    try:
        data = json_.read(file_path=path, b_use_suggested_converter=True)
    except Exception:
        logger.exception("Failed to read VLM endpoint stats from %s", path)
        return False
    if not isinstance(data, dict):
        return False
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, dict) or not endpoints:
        return False
    get_circuit_breaker().import_persistent_snapshot(endpoints)
    logger.info("Hydrated VLM endpoint stats from %s (%d endpoints)", path, len(endpoints))
    return True


def merge_endpoint_stats_for_api(states: Dict[str, dict], work_dir: Optional[str]) -> Dict[str, dict]:
    """API 层合并：内存态优先，磁盘补全缺失端点（便于重启后立即展示历史累计）。"""
    wd = (work_dir or "").strip()
    if not wd:
        return states
    path = stats_file_path(wd)
    if not os.path.isfile(path):
        return states
    try:
        data = json_.read(file_path=path, b_use_suggested_converter=True)
    except Exception:
        return states
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, dict):
        return states
    merged = dict(states)
    for eid, snap in endpoints.items():
        if not isinstance(snap, dict):
            continue
        mem = merged.get(eid)
        mem_calls = int((mem or {}).get("total_calls") or 0)
        disk_calls = int(snap.get("total_calls") or 0)
        if mem_calls >= disk_calls:
            continue
        disk_failures = snap.get("failure_timestamps") or []
        merged[eid] = {
            "tripped": bool(snap.get("tripped", False)),
            "tripped_until": float(snap.get("tripped_until") or 0),
            "failures_in_window": len(disk_failures) if isinstance(disk_failures, list) else 0,
            "total_calls": disk_calls,
            "failure_rate": round(
                (len(disk_failures) if isinstance(disk_failures, list) else 0)
                / max(disk_calls, 1),
                4,
            ),
            "last_error": str(snap.get("last_error") or ""),
        }
    return merged
