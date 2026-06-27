import os
import numpy as np
import cv2
from PIL import Image
from typing import Optional

# 定义旋转映射
ROTATION_MAP = {
    "ROTATE_90_CLOCKWISE": cv2.ROTATE_90_CLOCKWISE,
    "ROTATE_180": cv2.ROTATE_180,
    "ROTATE_90_COUNTERCLOCKWISE": cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def load_image(
        path: str,
        b_yuv: bool = False,
        yuv_type: str = "nv21",
        height: int = 0,
        width: int = 0,
        rotate_angle: Optional[str] = None
) -> Image.Image:
    """加载并处理单张图片 (支持 YUV 和旋转)"""
    if b_yuv:
        # 读取 YUV 数据
        with open(path, 'rb') as f:
            yuv_data = np.frombuffer(f.read(), dtype=np.uint8)

        if yuv_type.lower() == "nv21":
            # NV21: YYYY... VUVU...
            img_bgr = cv2.cvtColor(yuv_data.reshape((height * 3 // 2, width)), cv2.COLOR_YUV2BGR_NV21)
        elif yuv_type.lower() == "nv12":
            # NV12: YYYY... UVUV...
            img_bgr = cv2.cvtColor(yuv_data.reshape((height * 3 // 2, width)), cv2.COLOR_YUV2BGR_NV12)
        elif yuv_type.lower() == "yuv420p":
            # I420: YYYY... UU... VV...
            img_bgr = cv2.cvtColor(yuv_data.reshape((height * 3 // 2, width)), cv2.COLOR_YUV2BGR_I420)
        else:
            raise ValueError(f"Unsupported YUV type: {yuv_type}")

        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    else:
        # 普通图片读取
        img_bgr = cv2.imread(path)
        if img_bgr is None:
            raise ValueError(f"Failed to read image: {path}")
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    # 处理旋转
    if rotate_angle and rotate_angle in ROTATION_MAP:
        img_rgb = cv2.rotate(img_rgb, ROTATION_MAP[rotate_angle])

    return Image.fromarray(img_rgb)


def load_image_for_job(
    path: str,
    *,
    b_yuv_image: bool = False,
    mixed_yuv: bool = False,
    yuv_type: str = "nv21",
    image_height: int = 0,
    image_width: int = 0,
    rotate_angle: Optional[str] = None,
) -> Image.Image:
    """
    按任务配置加载单张图。
    mixed_yuv 为 True 时：.nv21/.nv12/.yuv 按 YUV 读取（类型由后缀或 yuv_type），其余按普通图像。
    """
    lower = path.lower()
    use_yuv = b_yuv_image
    eff_type = yuv_type
    eff_h, eff_w = image_height, image_width

    if mixed_yuv:
        if lower.endswith(".nv21"):
            use_yuv = True
            eff_type = "nv21"
        elif lower.endswith(".nv12"):
            use_yuv = True
            eff_type = "nv12"
        elif lower.endswith(".yuv"):
            use_yuv = True
            # 通用 .yuv 文件名常含格式提示（如 test_nv12.yuv）；若用错 nv21/nv12 会导致色度错位（偏蓝/偏红）
            stem = os.path.splitext(os.path.basename(path))[0].lower()
            if "nv12" in stem:
                eff_type = "nv12"
            elif "nv21" in stem:
                eff_type = "nv21"
            elif "yuv420" in stem or "i420" in stem or "420p" in stem:
                eff_type = "yuv420p"
            else:
                eff_type = yuv_type
        else:
            use_yuv = False

    return load_image(
        path,
        use_yuv,
        eff_type,
        eff_h,
        eff_w,
        rotate_angle,
    )


if __name__ == "__main__":
    import os

    # core/utils/load_image.py -> auto_tag 根
    _AUTO_TAG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    test_data_dir = os.path.join(_AUTO_TAG_ROOT, "test", "test_data")

    bmp_path = os.path.join(test_data_dir, "test.bmp")
    width, height = 0, 0
    if os.path.exists(bmp_path):
        with Image.open(bmp_path) as ref_img:
            width, height = ref_img.size

    test_images = ["test.bmp", "test.jpg", "test.png", "test.webp"]
    for img_name in test_images:
        img_path = os.path.join(test_data_dir, img_name)
        if os.path.exists(img_path):
            try:
                img = load_image(img_path)
                print(f"Successfully loaded {img_name}: format={img.format}, size={img.size}, mode={img.mode}")
            except Exception as e:
                print(f"Failed to load {img_name}: {e}")
        else:
            print(f"File not found: {img_path}")

    if width > 0 and height > 0:
        yuv_formats = ["nv21", "nv12", "yuv420p"]
        for fmt in yuv_formats:
            img_name = f"test_{fmt}.yuv"
            img_path = os.path.join(test_data_dir, img_name)
            if os.path.exists(img_path):
                try:
                    img = load_image(img_path, b_yuv=True, yuv_type=fmt, height=height, width=width)
                    print(f"Successfully loaded {img_name}: size={img.size}, mode={img.mode}")
                except Exception as e:
                    print(f"Failed to load {img_name}: {e}")
            else:
                print(f"File not found: {img_path}")
