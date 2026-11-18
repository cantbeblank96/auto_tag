#!/usr/bin/env python3
"""用标注同一条加载路径，复查 head_pose 的 7 张参考图和待标注图为什么没有人脸。

对照两组像素：
- pipeline：load_example_image_pil / load_image，不应用 EXIF Orientation
- upright：先 ImageOps.exif_transpose，再按同样最长边缩放
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASE = (
    ROOT
    / "data/10.29 bad case/head_pose/slight_pitch-frontal"
    / "0_0_2025.06.04-14.41.25.302.jpg"
)
SAMPLE = ROOT / "data/sample_image/head_pose"
# 与 take_example_images 的选取结果一致
REFS = [
    ("frontal", SAMPLE / "frontal/image_pitch-10_resized.jpg"),
    ("large_left yaw", SAMPLE / "large_left yaw/image_yaw50_resized.jpg"),
    ("large_pitch_down", SAMPLE / "large_pitch_down/image_pitch50_resized.jpg"),
    ("large_pitch_up", SAMPLE / "large_pitch_up/image_pitch-50_resized.jpg"),
    ("large_right yaw", SAMPLE / "large_right yaw/image_yaw-50_resized.jpg"),
    ("slight_pitch", SAMPLE / "slight_pitch/image_pitch-15_resized.jpg"),
    ("slight_yaw", SAMPLE / "slight_yaw/image_yaw-15_resized.jpg"),
]


def exif_orientation(path: Path) -> int | None:
    with Image.open(path) as im:
        exif = im.getexif()
    return exif.get(274) if exif else None


def resize_max_side(im: Image.Image, max_side: int) -> Image.Image:
    im = im.convert("RGB")
    w, h = im.size
    side = max(w, h)
    if side <= max_side:
        return im
    scale = float(max_side) / float(side)
    return im.resize(
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        Image.LANCZOS,
    )


def raw_detect(pil: Image.Image) -> list[dict]:
    import numpy as np
    from kevin_sdk.api import detect

    from auto_tag.core.annotation_tools import _detect_handle, _pil_to_bgr

    bgr = _pil_to_bgr(pil)
    rows = detect(image=bgr, model=_detect_handle()) or []
    out = []
    for it in rows:
        b = it.get("bbox") or {}
        out.append(
            {
                "score": round(float(it.get("score") or 0), 4),
                "bbox": {
                    "left": int(b.get("left", 0)),
                    "top": int(b.get("top", 0)),
                    "right": int(b.get("right", 0)),
                    "bottom": int(b.get("bottom", 0)),
                },
            }
        )
    return out


def pipeline_tools(pil: Image.Image) -> dict:
    from auto_tag.core.annotation_tools import run_tools_for_image

    return run_tools_for_image(pil, ["face_detect", "head_pose"])


def load_pipeline_example(path: Path):
    from auto_tag.core.config import settings
    from auto_tag.core.vlm_client import load_example_image_pil

    max_side = int(getattr(settings, "vlm_example_image_max_side", 512) or 512)
    im = load_example_image_pil(str(path), max_side)
    return im, max_side


def main() -> None:
    from auto_tag.core.utils.load_image import load_image

    items = [("target", CASE)] + REFS
    report = []
    for label, path in items:
        path = Path(path)
        ori = exif_orientation(path)
        with Image.open(path) as src:
            raw_size = list(src.size)
            upright = ImageOps.exif_transpose(src)
            upright_size = list(upright.size)
            upright = upright.convert("RGB")
        if label == "target":
            pip = load_image(str(path))
            max_side = None
        else:
            pip, max_side = load_pipeline_example(path)
        upright_scaled = resize_max_side(upright, 512 if max_side is None else max_side)
        row = {
            "label": label,
            "path": str(path),
            "exists": path.is_file(),
            "file_size": list(raw_size),
            "exif_orientation": ori,
            "upright_size_before_resize": upright_size,
            "pipeline_size": list(pip.size) if pip is not None else None,
            "pipeline_raw_detect": raw_detect(pip) if pip is not None else None,
            "pipeline_tools": pipeline_tools(pip) if pip is not None else None,
            "upright_size": list(upright_scaled.size),
            "upright_raw_detect": raw_detect(upright_scaled),
            "upright_tools": pipeline_tools(upright_scaled),
        }
        report.append(row)
        print(json.dumps(row, ensure_ascii=False))

    out = ROOT / "debug_from_cursor" / "head_pose_detect_recheck.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", out)


if __name__ == "__main__":
    main()
