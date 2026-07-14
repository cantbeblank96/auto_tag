"""流水线各环节耗时统计（仅在 debug 模式启用，正常路径几乎零开销）。"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


class PipelineProfile:
    """累计各阶段耗时；``enabled=False`` 时不记录。

    - ``thread_timings``：多线程场景下各 span 耗时**相加**（并行时会大于墙钟）。
    - ``wall_timings``：每次 span 入口到出口的**墙钟**，按逻辑调用累加（适合 VLM 单次打标）。
  """

    __slots__ = (
        "enabled",
        "thread_timings",
        "thread_counts",
        "wall_timings",
        "wall_counts",
        "wall_start",
        "wall_end",
        "_lock",
    )

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        self.thread_timings: Dict[str, float] = {}
        self.thread_counts: Dict[str, int] = {}
        self.wall_timings: Dict[str, float] = {}
        self.wall_counts: Dict[str, int] = {}
        self.wall_start: Optional[float] = None
        self.wall_end: Optional[float] = None
        self._lock = threading.Lock()

    def add_thread(self, name: str, seconds: float, *, count: int = 1) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.thread_timings[name] = self.thread_timings.get(name, 0.0) + max(
                0.0, seconds
            )
            self.thread_counts[name] = self.thread_counts.get(name, 0) + count

    def add_wall(self, name: str, seconds: float, *, count: int = 1) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.wall_timings[name] = self.wall_timings.get(name, 0.0) + max(
                0.0, seconds
            )
            self.wall_counts[name] = self.wall_counts.get(name, 0) + count

    def increment(self, name: str, *, count: int = 1) -> None:
        """仅计数（如 HTTP 调用次数）。"""
        if not self.enabled:
            return
        with self._lock:
            self.wall_counts[name] = self.wall_counts.get(name, 0) + count

    def add(self, name: str, seconds: float, *, count: int = 1) -> None:
        """兼容旧接口：写入 thread_timings。"""
        self.add_thread(name, seconds, count=count)

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        """记录线程累计耗时（并行时会相加）。"""
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add_thread(name, time.perf_counter() - t0)

    @contextmanager
    def span_wall(self, name: str) -> Iterator[None]:
        """记录单次调用的墙钟耗时（每次逻辑调用单独累加）。"""
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add_wall(name, time.perf_counter() - t0)

    def mark_wall_start(self) -> None:
        if self.enabled:
            self.wall_start = time.perf_counter()

    def mark_wall_end(self) -> None:
        if self.enabled:
            self.wall_end = time.perf_counter()

    def summary(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        wall = 0.0
        if self.wall_start is not None and self.wall_end is not None:
            wall = self.wall_end - self.wall_start
        thread_tracked = sum(self.thread_timings.values())
        wall_tracked = sum(self.wall_timings.values())

        stage_names = sorted(
            set(self.thread_timings) | set(self.wall_timings) | set(self.wall_counts),
            key=lambda n: -(
                self.wall_timings.get(n, 0.0) + self.thread_timings.get(n, 0.0)
            ),
        )
        rows = []
        for name in stage_names:
            w_sec = self.wall_timings.get(name, 0.0)
            w_cnt = self.wall_counts.get(name, 0)
            t_sec = self.thread_timings.get(name, 0.0)
            t_cnt = self.thread_counts.get(name, 0)
            row: Dict[str, Any] = {"stage": name}
            if w_sec > 0:
                row["wall_seconds"] = round(w_sec, 4)
                row["wall_count"] = w_cnt
                if w_cnt > 0:
                    row["avg_wall_seconds"] = round(w_sec / w_cnt, 4)
            elif w_cnt > 0:
                row["wall_count"] = w_cnt
            if t_sec > 0 or t_cnt > 0:
                row["thread_sum_seconds"] = round(t_sec, 4)
                row["thread_sum_count"] = t_cnt
                if t_sec > 0 and t_cnt > 0:
                    row["avg_thread_sum_seconds"] = round(t_sec / t_cnt, 4)
            if w_sec > 0 and thread_tracked > 0:
                row["pct_of_thread_tracked"] = round(100.0 * t_sec / thread_tracked, 1)
            rows.append(row)

        return {
            "enabled": True,
            "pipeline_wall_seconds": round(wall, 4),
            "thread_tracked_seconds": round(thread_tracked, 4),
            "wall_tracked_seconds": round(wall_tracked, 4),
            "stages": rows,
        }

    def log_summary(self) -> None:
        if not self.enabled:
            return
        s = self.summary()
        logger.info(
            "Pipeline profile: pipeline_wall=%.2fs wall_tracked=%.2fs thread_tracked=%.2fs",
            s.get("pipeline_wall_seconds", 0),
            s.get("wall_tracked_seconds", 0),
            s.get("thread_tracked_seconds", 0),
        )
        for row in s.get("stages", []):
            w = row.get("wall_seconds")
            w_avg = row.get("avg_wall_seconds")
            t_sum = row.get("thread_sum_seconds")
            if w is not None and w_avg is not None:
                logger.info(
                    "  [profile] %-22s wall=%8.2fs n=%-4s avg_wall=%.3fs",
                    row["stage"],
                    w,
                    row.get("wall_count", 0),
                    w_avg,
                )
            elif row.get("wall_count"):
                logger.info(
                    "  [profile] %-22s count=%s",
                    row["stage"],
                    row.get("wall_count"),
                )
            if t_sum is not None:
                logger.info(
                    "  [profile]   └ thread_sum=%.2fs n=%s",
                    t_sum,
                    row.get("thread_sum_count", 0),
                )


def resolve_pipeline_debug(cfg_flag: Optional[bool] = None) -> bool:
    """任务级覆盖 > config.json pipeline_debug > 环境变量 AUTO_TAG_PIPELINE_DEBUG。"""
    if cfg_flag is not None:
        return bool(cfg_flag)
    import os

    from auto_tag.core.config import settings

    env = os.environ.get("AUTO_TAG_PIPELINE_DEBUG", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    return bool(getattr(settings, "pipeline_debug", False))
