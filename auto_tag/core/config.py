import os
from typing import Any, Dict, List, Optional
from kevin_toolbox.data_flow.file import json_
from pydantic_settings import BaseSettings
from pydantic import Field

from auto_tag.core.circuit_breaker import CircuitBreakerConfig

# auto_tag 包根目录（与 config.json、.env 同级）
_AUTO_TAG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_PATH_MACRO = "{PROJECT_PATH}"
_DEFAULT_JSON = os.path.join(_AUTO_TAG_DIR, "config.json")


def _expand_project_path(s: str) -> str:
    """将 {PROJECT_PATH} 宏替换为 auto_tag 包目录的绝对路径。"""
    return s.replace(_PROJECT_PATH_MACRO, _AUTO_TAG_DIR)

# main.py 可在 import 本模块前设置 AUTO_TAG_CONFIG_FILE，以指定其它 JSON（与默认结构相同）
_config_json_path = os.environ.get("AUTO_TAG_CONFIG_FILE")
if _config_json_path:
    config_json_path = os.path.abspath(os.path.expanduser(_config_json_path))
else:
    config_json_path = _DEFAULT_JSON

cfg: dict = {}
if os.path.exists(config_json_path):
    cfg = json_.read(file_path=config_json_path, b_use_suggested_converter=True)


def _cfg_work_dir() -> Optional[str]:
    """从 config.json 读取 work_dir，返回绝对路径。支持 {PROJECT_PATH} 宏和绝对路径。"""
    val = cfg.get("work_dir")
    if val and str(val).strip():
        s = str(val).strip()
        # 先替换 {PROJECT_PATH} 宏
        s = _expand_project_path(s)
        if os.path.isabs(s):
            return os.path.realpath(s)
        # 相对路径以 auto_tag 包目录为基座
        return os.path.realpath(os.path.join(_AUTO_TAG_DIR, s))
    return os.path.realpath(os.path.join(_AUTO_TAG_DIR, "work_dir"))


def _cfg_embedding_subdir() -> str:
    return str(cfg.get("embedding_subdir") or "embedding_index")


def _cfg_db_path() -> str:
    """work_dir 存在时：{work_dir}/{embedding_subdir}，否则：./{embedding_subdir}。"""
    wd = _cfg_work_dir()
    if wd:
        return os.path.join(wd, _cfg_embedding_subdir())
    return f"./{_cfg_embedding_subdir()}"


def _cfg_vlm_models() -> List[Dict[str, Any]]:
    """从 config.json 读取多模型列表，若无则从 .env 回退为单模型。"""
    models = cfg.get("vlm_models")
    if isinstance(models, list) and len(models) > 0:
        return models
    # 向后兼容：从 .env 的单模型
    name = os.getenv("VLM_MODEL_NAME", "None")
    key = os.getenv("VLM_API_KEY", "None")
    if name and name != "None":
        return [{"name": name, "base_url": None, "api_key": key, "priority": 1}]
    return []


def _cfg_circuit_breaker() -> dict:
    cb = cfg.get("circuit_breaker") or {}
    return {
        "time_window_seconds": int(cb.get("time_window_seconds", 300)),
        "failure_rate_threshold": float(cb.get("failure_rate_threshold", 0.5)),
        "cooldown_seconds": int(cb.get("cooldown_seconds", 600)),
    }


class VlmModelConfig(BaseSettings):
    name: str = ""
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    priority: int = 1


class Settings(BaseSettings):
    # 工作根目录（来自 config.json）
    work_dir: Optional[str] = Field(
        default_factory=_cfg_work_dir,
        description="工作根目录（来自 config.json）；不传时由后端从 embedding_store_path 反向推导",
    )

    # 向量索引 fallback 路径（未指定 work_dir 时使用 ./{embedding_subdir}）
    db_path: str = Field(
        default_factory=_cfg_db_path,
        description="无 work_dir 时的默认 ChromaDB 路径，派生自 embedding_subdir",
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
    tau_cls: float = Field(default=cfg.get("tau_cls", 0.25),
                           description="Threshold for clustering (tau_dup < d <= tau_cls)")

    # VLM settings (single-model backwards compatibility)
    vlm_model_name: str = Field(default=os.getenv("VLM_MODEL_NAME", "None"), description="Model name for VLM API")
    vlm_api_key: str = Field(default=os.getenv("VLM_API_KEY", "None"), description="API key for VLM")

    # Multi-model settings
    vlm_models: List[Dict[str, Any]] = Field(
        default_factory=_cfg_vlm_models,
        description="List of VLM models with name/base_url/api_key/priority",
    )

    # Circuit breaker settings
    circuit_breaker_time_window: int = Field(
        default=_cfg_circuit_breaker()["time_window_seconds"],
        description="Circuit breaker monitoring window in seconds",
    )
    circuit_breaker_failure_threshold: float = Field(
        default=_cfg_circuit_breaker()["failure_rate_threshold"],
        description="Circuit breaker failure rate threshold (0.0-1.0)",
    )
    circuit_breaker_cooldown: int = Field(
        default=_cfg_circuit_breaker()["cooldown_seconds"],
        description="Circuit breaker cooldown duration in seconds",
    )

    # VLM strategy: "priority" or "round_robin"
    vlm_strategy: str = Field(
        default=cfg.get("vlm_strategy", "priority"),
        description="VLM calling strategy: priority (failover) or round_robin (load balance)",
    )

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
