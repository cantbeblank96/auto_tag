import logging
from typing import List
import torch
from PIL import Image
from transformers import CLIPProcessor, CLIPModel

logger = logging.getLogger(__name__)


def _cuda_usable() -> bool:
    """``torch.cuda.is_available()`` 为 True 时仍可能因算力/驱动与 wheel 不匹配而无法执行。"""
    if not torch.cuda.is_available():
        return False
    try:
        torch.zeros(1, device="cuda").add_(1)
        torch.cuda.synchronize()
        return True
    except Exception as e:
        logger.warning("CUDA 不可用（将回退 CPU）: %s", e)
        return False


def _resolve_device(preferred: str) -> str:
    pref = (preferred or "cpu").strip().lower()
    if pref == "cuda" and _cuda_usable():
        return "cuda"
    if pref == "cuda":
        logger.warning("配置为 cuda 但当前 GPU/PyTorch 无法执行 CUDA 算子，改用 CPU。")
    return "cpu"


class FeatureExtractor:
    def __init__(self, model_name: str, device: str = "cuda"):
        """
        初始化特征提取器 (使用 CLIP 模型)。

        Args:
            model_name: HuggingFace 模型名称
            device: 运行设备 ('cuda' 或 'cpu')
        """
        self.device = _resolve_device(device)
        logger.info(f"Loading CLIP model {model_name} on {self.device}...")
        try:
            self.model = CLIPModel.from_pretrained(model_name).to(self.device)
            self.processor = CLIPProcessor.from_pretrained(model_name)
            self.model.eval()
            logger.info("CLIP model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load CLIP model: {e}")
            raise

    def extract_features_batch(self, images: List[Image.Image]) -> List[List[float]]:
        """
        批量提取图像特征向量并进行 L2 归一化。

        Args:
            images: 预先加载好的 PIL Image 对象列表

        Returns:
            embeddings: 返回特征向量列表
        """
        if not images:
            logger.warning("No images in this batch to process.")
            return []

        try:
            # 前向传播提取特征（仅传 pixel_values，兼容 transformers 5.x 返回值）
            inputs = self.processor(images=images, return_tensors="pt")
            pixel_values = inputs["pixel_values"].to(self.device)
            with torch.no_grad():
                features = self.model.get_image_features(pixel_values=pixel_values)
                if not isinstance(features, torch.Tensor):
                    if hasattr(features, "pooler_output") and features.pooler_output is not None:
                        features = features.pooler_output
                    else:
                        raise TypeError(
                            f"Unexpected get_image_features return type: {type(features)}"
                        )
                # 必须进行 L2 归一化，以便计算余弦相似度
                features = features / features.norm(p=2, dim=-1, keepdim=True)

            embeddings = features.cpu().numpy().tolist()
            return embeddings

        except Exception as e:
            logger.error(f"Error during batch feature extraction: {e}")
            raise


if __name__ == "__main__":
    import os
    from auto_tag.core.utils import load_image
    logging.basicConfig(level=logging.INFO)

    extractor = FeatureExtractor(model_name="openai/clip-vit-base-patch32", device="cuda")

    test_img_path = "./test/test_data/test.bmp"

    if os.path.exists(test_img_path):
        try:
            img = load_image(path=test_img_path)

            embeddings = extractor.extract_features_batch([img])

            print(f"\nSuccessfully extracted features for: {test_img_path}")
            print(f"Embedding dimension: {len(embeddings[0])}")
            print(f"First 5 elements: {embeddings[0][:5]}")

        except Exception as e:
            print(f"Error during test: {e}")
    else:
        print(f"Test image not found at: {test_img_path}")
