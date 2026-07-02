"""CLI：导出紧凑标注 JSON（见 core.compact_labels_export）。"""
from __future__ import annotations

import argparse
import json
import os
import sys

from auto_tag.core.compact_labels_export import (
    build_compact_export,
    shared_compact_dict,
    slice_compact_chunk,
    slice_compact_parallel,
)


def main() -> None:
    p = argparse.ArgumentParser(description="导出紧凑标注（平行数组 + 共享字典）")
    p.add_argument(
        "--work_dir",
        default=os.environ.get("AUTO_TAG_WORK_DIR", os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "work_dir"
        )),
        help="工作根目录（含 log 与向量索引）",
    )
    p.add_argument(
        "--out",
        default="",
        help="完整 JSON 输出路径；若省略则打印到 stdout",
    )
    p.add_argument("--slice_offset", type=int, default=-1, help=">=0 时只写平行字段切片")
    p.add_argument("--slice_limit", type=int, default=100_000, help="与 slice_offset 联用")
    p.add_argument("--chunk_index", type=int, default=-1, help=">=0 时分块模式")
    p.add_argument("--chunk_size", type=int, default=100_000, help="与 chunk_index 联用")
    p.add_argument(
        "--shared_only",
        action="store_true",
        help="仅输出 labels/prefix/cluster/cluster_to_labels",
    )
    args = p.parse_args()

    full = build_compact_export(args.work_dir)
    if args.shared_only:
        out_obj = shared_compact_dict(full)
    elif args.chunk_index >= 0:
        out_obj = slice_compact_chunk(
            full, chunk_index=args.chunk_index, chunk_size=args.chunk_size
        )
    elif args.slice_offset >= 0:
        out_obj = slice_compact_parallel(
            full, offset=args.slice_offset, limit=args.slice_limit
        )
    else:
        out_obj = full

    text = json.dumps(out_obj, ensure_ascii=False, indent=2)
    if args.out:
        ap = os.path.abspath(args.out)
        parent = os.path.dirname(ap)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
