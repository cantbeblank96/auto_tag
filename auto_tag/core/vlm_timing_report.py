"""pipeline_debug 时序报告：Gantt 图、HTTP trace 文本与摘要 JSON。"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TIMING_JSON = "vlm_timing.json"
TIMING_PNG = "vlm_timing.png"
HTTP_TRACE_TXT = "vlm_http_trace.txt"
TIMING_SUMMARY_JSON = "vlm_timing_summary.json"


def _plot_label(path: str, fallback: str) -> str:
    """Matplotlib 标签仅用 ASCII，避免缺字；非 ASCII 文件名回退 cluster id。"""
    base = os.path.basename(path) if path else fallback
    try:
        base.encode("ascii")
        return base
    except UnicodeEncodeError:
        return fallback


def build_jobs(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    jobs: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for ev in events:
        cid = ev.get("cluster_id") or ""
        if not cid and ev.get("event") not in ("cluster_batch_done",):
            continue
        name = ev.get("event")
        t = float(ev.get("t", 0))
        if name == "job_enqueued":
            jobs[cid]["path"] = ev.get("path", cid)
            jobs[cid]["enqueued"] = t
        elif name == "job_worker_start":
            jobs[cid]["worker_start"] = t
            jobs[cid]["thread"] = ev.get("thread", "")
        elif name == "job_vlm_start":
            jobs[cid]["vlm_start"] = t
        elif name == "job_vlm_done":
            jobs[cid]["vlm_end"] = t
            jobs[cid]["vlm_s"] = ev.get("vlm_s")
        elif name == "job_done":
            jobs[cid]["done"] = t
    return dict(jobs)


def _pipeline_bounds(events: List[Dict[str, Any]]) -> Dict[str, float]:
    b: Dict[str, float] = {}
    for ev in events:
        if ev.get("event") in (
            "pipeline_start",
            "vlm_pool_drain_start",
            "vlm_pool_drain_done",
            "pipeline_end",
        ):
            b[ev["event"]] = float(ev["t"])
    return b


def _batch_markers(events: List[Dict[str, Any]]) -> List[Tuple[float, int]]:
    return [
        (float(ev["t"]), int(ev.get("new_centers", 0)))
        for ev in events
        if ev.get("event") == "cluster_batch_done"
    ]


def _classify_http_sequence(seq: List[Dict[str, Any]], timeout_s: float) -> List[str]:
    lines: List[str] = []
    pending_start: Optional[Dict[str, Any]] = None
    attempt = 0

    for ev in seq:
        event = ev.get("event", "")
        t = float(ev.get("t", 0))
        if event == "http_start":
            if pending_start is not None:
                gap = t - float(pending_start["t"])
                attempt += 1
                lines.append(
                    f"    [{attempt}] t={float(pending_start['t']):.3f}s  "
                    f"HTTP_START msg={pending_start.get('msg_count')} "
                    f"→ 无 http_done/http_failed，墙钟 {gap:.1f}s 后再次 HTTP_START "
                    f"【推断：读超时(≤{timeout_s:.0f}s)/网络错误后重试】"
                )
            pending_start = ev
        elif event == "http_failed":
            attempt += 1
            lines.append(
                f"    [{attempt}] t={t:.3f}s  HTTP_FAILED "
                f"elapsed={ev.get('elapsed_s')}s "
                f"type={ev.get('error_type')} "
                f"err={(ev.get('error') or '')[:120]}"
            )
            pending_start = None
        elif event == "http_done":
            attempt += 1
            chars = int(ev.get("resp_chars") or 0)
            elapsed = ev.get("elapsed_s")
            if pending_start is not None:
                wall = t - float(pending_start["t"])
                start_t = float(pending_start["t"])
            else:
                wall = float(elapsed or 0)
                start_t = t - wall
            kind = (
                "HTTP 已返回但 content 为空 → EmptyVLMResponseError → endpoint failover"
                if chars == 0
                else "HTTP 成功，收到 VLM 响应"
            )
            lines.append(
                f"    [{attempt}] t={start_t:.3f}s→{t:.3f}s  HTTP_DONE "
                f"elapsed={elapsed}s wall={wall:.1f}s resp_chars={chars}  【{kind}】"
            )
            pending_start = None
        elif event == "http_empty_failover":
            lines.append(
                f"    t={t:.3f}s  HTTP_EMPTY_FAILOVER turn={ev.get('turn')}"
            )

    if pending_start is not None:
        lines.append(
            f"    [?] t={float(pending_start['t']):.3f}s  HTTP_START "
            f"→ 仍无 http_done/http_failed"
        )
    return lines


def build_http_trace_text(data: Dict[str, Any], *, path_filter: str = "") -> str:
    """生成所有 VLM 任务的 HTTP 往返 trace 文本。"""
    events = data.get("events") or []
    meta = data.get("meta") or {}
    timeout_s = float(meta.get("vlm_http_timeout") or 60)
    jobs_meta: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        cid = ev.get("cluster_id") or ""
        if not cid:
            continue
        j = jobs_meta.setdefault(
            cid,
            {"path": ev.get("path") or "", "thread": "", "vlm_start": 0.0, "vlm_end": 0.0, "vlm_s": 0.0},
        )
        if ev.get("path"):
            j["path"] = ev["path"]
        if ev.get("event") == "job_vlm_start":
            j["thread"] = ev.get("thread", "")
            j["vlm_start"] = float(ev["t"])
        if ev.get("event") == "job_vlm_done":
            j["vlm_end"] = float(ev["t"])
            j["vlm_s"] = float(ev.get("vlm_s") or 0)

    http_by_thread: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        if ev.get("event") in (
            "http_start",
            "http_done",
            "http_failed",
            "http_empty_failover",
        ):
            http_by_thread[ev.get("thread", "")].append(ev)

    lines = [
        f"meta: concurrency={meta.get('vlm_concurrency')} "
        f"timeout={timeout_s}s total_images={meta.get('total_images')}",
        f"说明: 读超时 = httpx 在 {timeout_s:.0f}s 内未收到 VLM API 响应体。",
        "",
    ]
    for cid, j in sorted(jobs_meta.items(), key=lambda x: -x[1].get("vlm_s", 0)):
        path = j.get("path", cid)
        if path_filter and path_filter not in path:
            continue
        th = j.get("thread", "")
        vlm_start, vlm_end = j.get("vlm_start", 0.0), j.get("vlm_end", 0.0)
        seq = [
            e
            for e in http_by_thread.get(th, [])
            if vlm_start - 0.05 <= float(e["t"]) <= vlm_end + 0.05
        ]
        lines.append("=" * 72)
        lines.append(f"任务: {path}")
        lines.append(
            f"worker: {th}  vlm 窗口: {vlm_start:.3f}s ~ {vlm_end:.3f}s  "
            f"合计 vlm_s={j.get('vlm_s')}"
        )
        lines.append("HTTP 明细:")
        if not seq:
            lines.append("  （该 vlm 窗口内无 http 事件）")
        else:
            lines.extend(_classify_http_sequence(seq, timeout_s))
        lines.append("")
    return "\n".join(lines)


def build_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    events = data.get("events") or []
    meta = data.get("meta") or {}
    jobs = build_jobs(events)
    bounds = _pipeline_bounds(events)
    vlm_times = [float(j.get("vlm_s") or 0) for j in jobs.values() if j.get("vlm_s") is not None]
    http_failed = [e for e in events if e.get("event") == "http_failed"]
    wall = bounds.get("pipeline_end")
    drain = None
    if bounds.get("vlm_pool_drain_done") is not None and bounds.get("vlm_pool_drain_start") is not None:
        drain = round(bounds["vlm_pool_drain_done"] - bounds["vlm_pool_drain_start"], 2)
    job_rows = sorted(
        [
            {"path": j.get("path", cid), "vlm_s": j.get("vlm_s"), "thread": j.get("thread", "")}
            for cid, j in jobs.items()
        ],
        key=lambda r: -(float(r.get("vlm_s") or 0)),
    )
    return {
        "meta": meta,
        "pipeline_wall_s": round(wall, 2) if wall is not None else None,
        "vlm_pool_drain_s": drain,
        "job_count": len(jobs),
        "avg_vlm_s": round(sum(vlm_times) / len(vlm_times), 2) if vlm_times else None,
        "max_vlm_s": round(max(vlm_times), 2) if vlm_times else None,
        "http_done_count": sum(1 for e in events if e.get("event") == "http_done"),
        "http_failed_count": len(http_failed),
        "http_failed": [
            {
                "t": e.get("t"),
                "thread": e.get("thread"),
                "elapsed_s": e.get("elapsed_s"),
                "error_type": e.get("error_type"),
            }
            for e in http_failed
        ],
        "jobs_by_vlm_s": job_rows,
    }


def plot_timing_gantt(
    data: Dict[str, Any],
    out_path: str,
    title: str = "VLM annotation timeline",
) -> None:
    """Draw Gantt PNG (English labels only for reliable rendering)."""
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    events = data.get("events") or []
    meta = data.get("meta") or {}
    jobs = build_jobs(events)
    bounds = _pipeline_bounds(events)
    batches = _batch_markers(events)

    ordered = sorted(
        jobs.items(),
        key=lambda kv: kv[1].get("enqueued", kv[1].get("worker_start", 0)),
    )
    if not ordered:
        raise ValueError("no VLM jobs in timing data")

    t_max = max(
        [v.get("done", v.get("vlm_end", 0)) for _, v in ordered]
        + [bounds.get("pipeline_end", 0), bounds.get("vlm_pool_drain_done", 0)]
    ) * 1.02

    fig, axes = plt.subplots(
        2, 1, figsize=(14, max(6, len(ordered) * 0.35 + 3)), height_ratios=[2, 1]
    )
    ax_jobs, ax_workers = axes
    colors = {
        "wait": "#CBD5E1",
        "load": "#93C5FD",
        "vlm": "#F59E0B",
        "apply": "#34D399",
    }
    y_labels = []
    for i, (cid, j) in enumerate(ordered):
        path = j.get("path", cid)
        y_labels.append(_plot_label(str(path), cid[-12:] if cid else f"job{i}"))
        enq = j.get("enqueued", j.get("worker_start", 0))
        ws = j.get("worker_start", enq)
        vs = j.get("vlm_start", ws)
        ve = j.get("vlm_end", vs)
        done = j.get("done", ve)
        if ws > enq:
            ax_jobs.barh(i, ws - enq, left=enq, height=0.6, color=colors["wait"], edgecolor="white")
        if vs > ws:
            ax_jobs.barh(i, vs - ws, left=ws, height=0.6, color=colors["load"], edgecolor="white")
        if ve > vs:
            ax_jobs.barh(i, ve - vs, left=vs, height=0.6, color=colors["vlm"], edgecolor="white")
        if done > ve:
            ax_jobs.barh(i, done - ve, left=ve, height=0.6, color=colors["apply"], edgecolor="white")
        vlm_s = j.get("vlm_s")
        if vlm_s is not None:
            ax_jobs.text(ve + 0.5, i, f"{vlm_s:.0f}s", va="center", fontsize=7, color="#666")

    for t, _nc in batches:
        ax_jobs.axvline(t, color="#6366F1", linestyle="--", alpha=0.5, linewidth=0.8)
    if bounds.get("vlm_pool_drain_start"):
        ax_jobs.axvline(bounds["vlm_pool_drain_start"], color="#EF4444", linestyle=":", alpha=0.7, linewidth=1)
    if bounds.get("vlm_pool_drain_done"):
        ax_jobs.axvline(bounds["vlm_pool_drain_done"], color="#EF4444", linestyle="-", alpha=0.7, linewidth=1)

    timeout_note = meta.get("vlm_http_timeout")
    ax_jobs.set_yticks(range(len(y_labels)))
    ax_jobs.set_yticklabels(y_labels, fontsize=8)
    ax_jobs.set_xlim(0, t_max)
    ax_jobs.set_xlabel("seconds since pipeline start")
    wall_s = bounds.get("pipeline_end", t_max)
    ax_jobs.set_title(
        f"{title}\n"
        f"concurrency={meta.get('vlm_concurrency')} | "
        f"timeout={timeout_note}s | centers={len(ordered)} | "
        f"wall={wall_s:.0f}s"
    )
    ax_jobs.invert_yaxis()
    ax_jobs.grid(axis="x", alpha=0.3)
    ax_jobs.legend(
        handles=[
            mpatches.Patch(color=colors["wait"], label="queue wait"),
            mpatches.Patch(color=colors["load"], label="load image"),
            mpatches.Patch(color=colors["vlm"], label="VLM HTTP"),
            mpatches.Patch(color=colors["apply"], label="write DB"),
        ],
        loc="lower right",
        fontsize=8,
    )

    threads = sorted({j.get("thread", "") for _, j in ordered if j.get("thread")})
    thread_y = {th: i for i, th in enumerate(threads)}
    for cid, j in ordered:
        th = j.get("thread", "")
        if not th:
            continue
        y = thread_y[th]
        vs = j.get("vlm_start", j.get("worker_start", 0))
        ve = j.get("vlm_end", vs)
        ax_workers.barh(y, ve - vs, left=vs, height=0.55, color=colors["vlm"], edgecolor="white")
        short = _plot_label(str(j.get("path") or ""), cid[-12:] if cid else "")[:12]
        ax_workers.text(vs + 0.2, y, short, va="center", fontsize=6)

    for t, _ in batches:
        ax_workers.axvline(t, color="#6366F1", linestyle="--", alpha=0.5, linewidth=0.8)

    ax_workers.set_yticks(range(len(threads)))
    ax_workers.set_yticklabels(threads, fontsize=8)
    ax_workers.set_xlim(0, t_max)
    ax_workers.set_xlabel("seconds since pipeline start")
    ax_workers.set_title("worker lanes (VLM segment only)")
    ax_workers.invert_yaxis()
    ax_workers.grid(axis="x", alpha=0.3)

    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def write_debug_artifacts(
    log_dir: str,
    *,
    title: str = "VLM annotation timeline",
    timing_json_name: str = TIMING_JSON,
    artifact_stem: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    读取 log_dir 下 timing JSON，写出 PNG / HTTP trace / 摘要 JSON。

    artifact_stem 默认取 timing_json_name 去掉 .json 的后缀。
    """
    json_path = os.path.join(log_dir, timing_json_name)
    stem = artifact_stem or os.path.splitext(timing_json_name)[0]
    png_name = f"{stem}.png" if stem != "vlm_timing" else TIMING_PNG
    trace_name = f"{stem}_http_trace.txt" if stem != "vlm_timing" else HTTP_TRACE_TXT
    summary_name = f"{stem}_summary.json" if stem != "vlm_timing" else TIMING_SUMMARY_JSON
    out: Dict[str, Optional[str]] = {
        "timing_json": json_path if os.path.isfile(json_path) else None,
        "timing_png": None,
        "http_trace_txt": None,
        "timing_summary_json": None,
    }
    if not os.path.isfile(json_path):
        logger.warning("Timing JSON not found: %s", json_path)
        return out

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    summary = build_summary(data)
    summary_path = os.path.join(log_dir, summary_name)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    out["timing_summary_json"] = summary_path

    trace_path = os.path.join(log_dir, trace_name)
    with open(trace_path, "w", encoding="utf-8") as f:
        f.write(build_http_trace_text(data))
    out["http_trace_txt"] = trace_path

    png_path = os.path.join(log_dir, png_name)
    try:
        plot_timing_gantt(data, png_path, title=title)
        out["timing_png"] = png_path
    except ValueError as e:
        logger.warning("Skip timing Gantt (no VLM jobs): %s", e)
    except ImportError:
        logger.warning(
            "matplotlib not installed; skip %s (pip install matplotlib)",
            TIMING_PNG,
        )
    except Exception as e:
        logger.warning("Failed to write timing Gantt: %s", e)

    summary["artifacts"] = {k: v for k, v in out.items() if v}
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return out
