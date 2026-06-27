import os
from kevin_toolbox.data_flow.file import json_
from pydantic_settings import BaseSettings
from pydantic import Field

# auto_tag 包根目录（与 config.json、.env 同级）
_AUTO_TAG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_JSON = os.path.join(_AUTO_TAG_DIR, "config.json")

# main.py 可在 import 本模块前设置 AUTO_TAG_CONFIG_FILE，以指定其它 JSON（与默认结构相同）
_config_json_path = os.environ.get("AUTO_TAG_CONFIG_FILE")
if _config_json_path:
    config_json_path = os.path.abspath(os.path.expanduser(_config_json_path))
else:
    config_json_path = _DEFAULT_JSON

cfg: dict = {}
if os.path.exists(config_json_path):
    cfg = json_.read(file_path=config_json_path, b_use_suggested_converter=True)


def _cfg_embedding_store_path() -> str:
    """默认向量索引持久化路径（兼容旧键 chroma_data）。"""
    return str(
        cfg.get("embedding_store_path")
        or cfg.get("chroma_data")
        or "./embedding_index"
    )


def _cfg_embedding_subdir() -> str:
    """work_dir 下存放向量索引的子目录名（兼容旧默认 chroma_data）。"""
    return str(cfg.get("embedding_subdir") or "embedding_index")


class Settings(BaseSettings):
    # 向量索引根路径（未指定 work_dir 时；与 work_dir 任务内子目录不同，见 embedding_subdir）
    db_path: str = Field(
        default=_cfg_embedding_store_path(),
        description="默认向量库持久化目录（功能命名；实现上可为 Chroma 等后端）",
    )
    embedding_subdir: str = Field(
        default=_cfg_embedding_subdir(),
        description="work_dir 下向量索引子目录名；若仅有旧版 chroma_data 目录则自动回退",
    )
    collection_name: str = Field(default="image_embeddings", description="ChromaDB collection name")

    # Model settings
    clip_model_name: str = Field(default="openai/clip-vit-base-patch32", description="HuggingFace model name for CLIP")
    device: str = Field(default="cuda", description="Device to run CLIP on (cuda/cpu)")
    batch_size: int = Field(default=cfg.get("batch_size", 32), description="Batch size for feature extraction")

    # Clustering thresholds
    tau_dup: float = Field(default=cfg.get("tau_dup", 0.05),
                           description="Threshold for duplication (d <= tau_dup)")
    tau_cls: float = Field(default=cfg.get("tau_cls", 0.15),
                           description="Threshold for clustering (tau_dup < d <= tau_cls)")

    # VLM settings
    vlm_model_name: str = Field(default=os.getenv("VLM_MODEL_NAME", "None"), description="Model name for VLM API")
    vlm_api_key: str = Field(default=os.getenv("VLM_API_KEY", "None"), description="API key for VLM")

    # Dynamic Questions
    questions: dict = Field(default=cfg.get("questions", {}), description="Dynamic questions for VLM structured output")

    # Stage 1 冗余侧车（默认 SQLite，亦可配置 .jsonl），路径为任务 work_dir/log/
    record_stage1_duplicates: bool = Field(
        default=cfg.get("record_stage1_duplicates", True),
        description="Write duplicate pairs under work_dir/log when d <= tau_dup",
    )
    duplicate_links_filename: str = Field(
        default=cfg.get("duplicate_links_filename", "duplicate_links.sqlite"),
        description="Filename under work_dir/log (.sqlite / .db 或 .jsonl)",
    )

    class Config:
        env_file = os.path.join(_AUTO_TAG_DIR, ".env")
        env_file_encoding = "utf-8"


settings = Settings()
