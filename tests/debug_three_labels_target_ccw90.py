#!/usr/bin/env python3
"""改进参考图逻辑后的三标签测试。

待标注图默认逆时针旋转 90 度后再进入检测工具和大模型。
参考图仍由标注流程按 EXIF 转正；工具用原尺寸，大模型用缩小后的 JPEG。

用法（仓库根目录）:
    uv run python tests/debug_three_labels_target_ccw90.py
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "debug_one_case_three_labels.py"
RUN_DIR = "debug_from_cursor/three_labels_target_ccw90"

if __name__ == "__main__":
    sys.argv = [
        str(SCRIPT),
        "--rotate-ccw",
        "90",
        "--run-dir",
        RUN_DIR,
        *sys.argv[1:],
    ]
    runpy.run_path(str(SCRIPT), run_name="__main__")
