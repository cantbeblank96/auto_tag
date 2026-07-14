#!/usr/bin/env python3
"""从 vlm_timing.json 绘制 VLM 标注时序 Gantt 图（CLI 包装）。"""
from __future__ import annotations

import argparse
import json
import sys

REPO = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
sys.path.insert(0, REPO)

from auto_tag.core.vlm_timing_report import plot_timing_gantt  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Plot VLM timing Gantt from vlm_timing.json")
    p.add_argument("json_path", help="path to vlm_timing.json")
    p.add_argument("-o", "--output", help="output PNG path")
    p.add_argument("-t", "--title", default="VLM annotation timeline")
    args = p.parse_args()
    out = args.output or args.json_path.replace(".json", ".png")
    with open(args.json_path, encoding="utf-8") as f:
        data = json.load(f)
    plot_timing_gantt(data, out, args.title)
    print("wrote", out)


if __name__ == "__main__":
    main()
