import os
import cv2
import numpy as np
from PIL import Image


def generate_yuv_from_bgr(img_bgr, format_name):
    height, width = img_bgr.shape[:2]
    # Ensure dimensions are even for YUV420 formats
    img_bgr = img_bgr[:height - (height % 2), :width - (width % 2)]
    height, width = img_bgr.shape[:2]

    yuv_i420 = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YUV_I420)

    yuv_flat = yuv_i420.reshape(-1)
    y_size = height * width
    uv_size = y_size // 4

    y = yuv_flat[:y_size]
    u = yuv_flat[y_size:y_size + uv_size]
    v = yuv_flat[y_size + uv_size:]

    if format_name == "yuv420p":
        return yuv_i420.tobytes()  # YYYY... UU... VV...
    elif format_name == "nv12":
        # YYYY... UVUV...
        uv = np.empty(len(u) + len(v), dtype=np.uint8)
        uv[0::2] = u
        uv[1::2] = v
        return np.concatenate((y, uv)).tobytes()
    elif format_name == "nv21":
        # YYYY... VUVU...
        vu = np.empty(len(u) + len(v), dtype=np.uint8)
        vu[0::2] = v
        vu[1::2] = u
        return np.concatenate((y, vu)).tobytes()
    else:
        raise ValueError(f"Unknown format: {format_name}")


def generate_images():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    bmp_path = os.path.join(base_dir, "test.bmp")

    if not os.path.exists(bmp_path):
        # Create a dummy bmp if it doesn't exist just for the test to work
        # Make it even size 100x100 for easy YUV conversion
        img = Image.new('RGB', (100, 100), color='red')
        img.save(bmp_path)
        print(f"Created dummy {bmp_path}")

    try:
        # Generate standard image formats
        img = Image.open(bmp_path)
        # Resize to an even dimension if not already to prevent YUV chroma alignment issues
        if img.width % 2 != 0 or img.height % 2 != 0:
            img = img.resize((img.width - (img.width % 2), img.height - (img.height % 2)))
            img.save(bmp_path)  # Update bmp to even sizes

        img.save(os.path.join(base_dir, "test.jpg"))
        img.save(os.path.join(base_dir, "test.png"))
        img.save(os.path.join(base_dir, "test.webp"))
        print("Successfully generated test.jpg, test.png, and test.webp")

        # Generate YUV formats
        img_bgr = cv2.imread(bmp_path)
        yuv_formats = ["nv21", "nv12", "yuv420p"]
        for fmt in yuv_formats:
            yuv_data = generate_yuv_from_bgr(img_bgr, fmt)
            yuv_path = os.path.join(base_dir, f"test_{fmt}.yuv")
            with open(yuv_path, "wb") as f:
                f.write(yuv_data)
            print(f"Successfully generated {yuv_path}")

    except Exception as e:
        print(f"Failed to generate images: {e}")


if __name__ == "__main__":
    generate_images()
