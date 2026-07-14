#!/usr/bin/env python3
"""对比并发 3 vs 10：跑 little_data 全集并生成 VLM 时序图。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from auto_tag.core.config import reload_settings_from_disk, settings  # noqa: E402
from auto_tag.core.pipeline import (  # noqa: E402
    PipelineConfig,
    normalize_work_dir,
    run_annotation_pipeline,
    work_embedding_store_dir,
    work_log_dir,
)


def run_once(concurrency: int, tag: str) -> dict:
    object.__setattr__(settings, "vlm_concurrency", concurrency)
    wd = normalize_work_dir("")
    log_d = work_log_dir(wd)
    emb_d = work_embedding_store_dir(wd)
    if os.path.isdir(emb_d):
        shutil.rmtree(emb_d)
    os.makedirs(emb_d, exist_ok=True)

    cfg = PipelineConfig(
        input_dirs=["/home/SENSETIME/xukaiming/Desktop/temp/little_data"],
        work_dir="",
        rotate_angle="ROTATE_90_COUNTERCLOCKWISE",
        mixed_yuv=True,
        yuv_type="nv21",
        image_width=640,
        image_height=480,
        skip_if_in_db=False,
        pipeline_debug=True,
    )
    t0 = time.time()
    result = run_annotation_pipeline(cfg)
    wall = time.time() - t0

    src = os.path.join(log_d, "vlm_timing.json")
    dst = os.path.join(log_d, f"vlm_timing_{tag}.json")
    if os.path.isfile(src):
        shutil.copy2(src, dst)

    ps = result.profile_summary or {}
    summary = {
        "tag": tag,
        "concurrency": concurrency,
        "wall_s": round(wall, 2),
        "pipeline_wall": ps.get("pipeline_wall_seconds"),
        "timing_json": dst,
    }
    for row in ps.get("stages", []):
        if row.get("stage") in ("vlm_annotate", "vlm_http_calls", "vlm_pool_drain"):
            summary[row["stage"]] = row
    return summary


def main() -> None:
    reload_settings_from_disk()
    log_d = work_log_dir(normalize_work_dir(""))
    summaries = []
    for c, tag in ((3, "c3"), (10, "c10")):
        print(f"\n===== run concurrency={c} =====")
        summaries.append(run_once(c, tag))
        json_path = os.path.join(log_d, f"vlm_timing_{tag}.json")
        from auto_tag.core.vlm_timing_report import write_debug_artifacts  # noqa: E402

        write_debug_artifacts(
            log_d,
            title=f"little_data n=172 | concurrency={c}",
            timing_json_name=f"vlm_timing_{tag}.json",
        )

    cmp_path = os.path.join(log_d, "vlm_timing_compare.json")
    with open(cmp_path, "w", encoding="utf-8") as f:
        json.dump(summaries, f, ensure_ascii=False, indent=2)
    print("\n=== compare ===")
    for s in summaries:
        print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()
