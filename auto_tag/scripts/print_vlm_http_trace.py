#!/usr/bin/env python3
"""解析 vlm_timing.json，打印每个标注任务的 HTTP 往返明细（CLI 包装）。"""
from __future__ import annotations

import argparse
import json
import sys

REPO = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
sys.path.insert(0, REPO)

from auto_tag.core.vlm_timing_report import build_http_trace_text  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="打印 VLM HTTP 往返 trace")
    parser.add_argument("timing_json", help="vlm_timing.json 路径")
    parser.add_argument(
        "--path",
        default="",
        help="只打印路径包含此子串的任务，如 1970.02.25",
    )
    args = parser.parse_args()
    with open(args.timing_json, encoding="utf-8") as f:
        data = json.load(f)
    print(build_http_trace_text(data, path_filter=args.path))


if __name__ == "__main__":
    main()
