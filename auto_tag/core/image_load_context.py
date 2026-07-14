"""流水线任务级图像加载参数，供 CLIP 批处理与异步 VLM worker 共用。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from PIL import Image

from auto_tag.core.utils.load_image import load_image_for_job


@dataclass(frozen=True)
class ImageLoadContext:
    """与 PipelineConfig 对齐的解码/旋转参数。"""

    b_yuv_image: bool = False
    mixed_yuv: bool = False
    yuv_type: str = "nv21"
    image_height: int = 0
    image_width: int = 0
    rotate_angle: Optional[str] = None

    def load_path(self, path: str, decode_meta: Optional[Dict[str, Any]] = None) -> Image.Image:
        """按任务配置与单条 decode_meta 加载图片。"""
        meta = decode_meta or {}
        media_kind = str(meta.get("media_kind") or "rgb")
        if self.mixed_yuv:
            return load_image_for_job(
                path,
                b_yuv_image=False,
                mixed_yuv=True,
                yuv_type=self.yuv_type,
                image_height=self.image_height,
                image_width=self.image_width,
                rotate_angle=self.rotate_angle,
            )
        if self.b_yuv_image or media_kind == "yuv":
            return load_image_for_job(
                path,
                b_yuv_image=True,
                mixed_yuv=False,
                yuv_type=str(meta.get("yuv_layout") or self.yuv_type),
                image_height=int(meta.get("pix_h") or self.image_height),
                image_width=int(meta.get("pix_w") or self.image_width),
                rotate_angle=self.rotate_angle,
            )
        return load_image_for_job(
            path,
            b_yuv_image=False,
            mixed_yuv=False,
            yuv_type=self.yuv_type,
            image_height=self.image_height,
            image_width=self.image_width,
            rotate_angle=self.rotate_angle,
        )
