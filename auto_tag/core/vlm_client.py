"""
VLM 客户端：支持多模型 Failover + Circuit Breaker。

支持两种模式：
1. VLMClient(model_name=..., api_key=...) — 兼容旧单模型模式
2. VLMClient(models=[...], circuit_breaker=...) — 多模型 Failover

通过直接 HTTP 调用 OpenAI 兼容接口，不再依赖 litellm。
"""
import base64
import io
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from PIL import Image

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from auto_tag.core.vlm_timing_collector import record as timing_record
from auto_tag.core.vlm_timing_collector import is_enabled as timing_enabled

from auto_tag.core.circuit_breaker import CircuitBreaker, get_circuit_breaker
from auto_tag.core.vlm_model_utils import vlm_model_endpoint_id

if TYPE_CHECKING:
    from auto_tag.core.pipeline_profile import PipelineProfile

logger = logging.getLogger(__name__)


def encode_pil_image_to_base64(image: Image.Image) -> str:
    buffered = io.BytesIO()
    image.convert('RGB').save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def resize_image_for_vlm(image: Image.Image, max_side: int) -> Image.Image:
    """待标注图送入 VLM 前的缩放：最长边缩到 max_side（只降不升）；
    max_side<=0（不缩放）或无需缩放时返回原图。"""
    try:
        max_side = int(max_side or 0)
    except (TypeError, ValueError):
        return image
    if max_side <= 0:
        return image
    w, h = image.size
    side = max(w, h)
    if side <= max_side:
        return image
    scale = float(max_side) / float(side)
    return image.resize(
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        Image.LANCZOS,
    )


# ── image-in-prompt：questions 中 examples 参考样图 ────────────────

# 样图 base64 缓存：(绝对路径, mtime, max_side) -> base64（加载失败的负结果也缓存，避免重复警告）
_EXAMPLE_IMAGE_CACHE: Dict[Tuple[str, float, int], Optional[str]] = {}
_EXAMPLE_IMAGE_CACHE_LOCK = threading.Lock()
# 缺失样图告警去重：每路径仅告警一次，避免大批量任务刷屏
_EXAMPLE_IMAGE_WARNED: set = set()


def resolve_example_path(path: str) -> str:
    """解析 questions examples 中的样图路径：绝对路径直接用；相对路径基于 config.json 所在目录。"""
    p = os.path.expanduser(str(path or ""))
    if os.path.isabs(p):
        return p
    from auto_tag.core.config import config_json_path

    return os.path.normpath(os.path.join(os.path.dirname(config_json_path), p))


def load_example_image_base64(path: str, max_side: int) -> Optional[str]:
    """加载参考样图 → RGB → 最长边缩放到 max_side → JPEG base64；失败返回 None。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    key = (path, mtime, int(max_side))
    with _EXAMPLE_IMAGE_CACHE_LOCK:
        if key in _EXAMPLE_IMAGE_CACHE:
            return _EXAMPLE_IMAGE_CACHE[key]
    b64: Optional[str] = None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            side = max(w, h)
            if side > max_side:
                scale = float(max_side) / float(side)
                im = im.resize(
                    (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                    Image.LANCZOS,
                )
            buffered = io.BytesIO()
            im.save(buffered, format="JPEG", quality=85)
            b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
    except Exception as e:
        logger.warning("Failed to load reference example image %s: %s", path, e)
        b64 = None
    with _EXAMPLE_IMAGE_CACHE_LOCK:
        _EXAMPLE_IMAGE_CACHE[key] = b64
    return b64


def _example_value_sort_key(value: Any) -> Tuple[int, float, str]:
    """样图按档位值排序：数值型按数值，其余按字符串，保证 prompt 中档位有序。"""
    try:
        return (0, float(value), str(value))
    except (TypeError, ValueError):
        return (1, 0.0, str(value))


class AllModelsFailedError(Exception):
    """所有模型均失败时抛出。"""
    pass


class EmptyVLMResponseError(Exception):
    """VLM HTTP 200 但 content 为空。"""
    pass


class VLMValidationError(Exception):
    """VLM 输出经多轮纠正后仍非法（校验不通过或 JSON 无法解析）。"""
    pass


def openai_chat_completion(
    model: str,
    messages: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None,
    max_tokens: int = 4096,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """
    直接调用 OpenAI 兼容的 Chat Completions API，替代 litellm。

    Args:
        model: 模型名称
        messages: 消息列表
        api_key: API Key（可选）
        base_url: Base URL（可选，默认 https://api.openai.com/v1）
        response_format: 响应格式（可选，如 {"type": "json_object"}）
        max_tokens: 最大 token 数
        timeout: 超时秒数

    Returns:
        API 响应的 JSON 字典
    """
    url = _build_chat_url(base_url)
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if response_format:
        body["response_format"] = response_format

    logger.debug(f"POST {url} model={model}")
    with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
        resp = client.post(url, headers=headers, json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # 附带状态码与服务端错误信息片段，便于任务日志定位
            try:
                body_snippet = (resp.text or "")[:300]
            except Exception:
                body_snippet = ""
            e.args = (
                f"HTTP {resp.status_code} from {url}: {body_snippet}",
                e.response,
            )
            raise
        return resp.json()


def _extract_finish_reason(response_json: Dict[str, Any]) -> str:
    """从 OpenAI 响应中提取 finish_reason（截断检测用）。"""
    try:
        return str(response_json["choices"][0].get("finish_reason") or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _build_chat_url(base_url: Optional[str]) -> str:
    if not base_url:
        return "https://api.openai.com/v1/chat/completions"
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    if not url.endswith("/chat/completions"):
        return f"{url}/chat/completions"
    return url


# 模型最大输出长度解析结果缓存：(base_url, model_name) -> (值或None, 时间戳)
_MAX_OUTPUT_CACHE: Dict[Tuple[str, str], Tuple[Optional[int], float]] = {}
_MAX_OUTPUT_CACHE_LOCK = threading.Lock()
_MAX_OUTPUT_FALLBACK = 8192


def _lookup_model_max_output_tokens(model: Dict[str, Any]) -> Optional[int]:
    """查询模型条目声明的最大输出长度；失败或未声明时返回 None（不写缓存）。"""
    base_url = (model.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    name = str(model.get("name") or "")
    if not name:
        return None
    headers = {}
    if model.get("api_key"):
        headers["Authorization"] = f"Bearer {model['api_key']}"
    resp = httpx.get(f"{base_url}/models", headers=headers, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else payload
    for item in data or []:
        if not isinstance(item, dict):
            continue
        if item.get("id") != name and item.get("name") != name:
            continue
        for field in ("max_output_length", "max_output_tokens", "max_tokens"):
            v = item.get(field)
            if isinstance(v, (int, float)) and v > 0:
                return max(1, min(131072, int(v)))
        break
    return None


def resolve_model_max_output_tokens(
    model: Dict[str, Any], *, fallback: int = _MAX_OUTPUT_FALLBACK
) -> int:
    """查询模型支持的最大输出长度（GET {base_url}/models），失败时回退 fallback。

    依次尝试读取条目中的 max_output_length / max_output_tokens / max_tokens 字段；
    成功结果常驻缓存，失败结果缓存 5 分钟避免频繁请求。
    """
    base_url = (model.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    name = str(model.get("name") or "")
    if not name:
        return fallback
    key = (base_url, name)
    now = time.time()
    with _MAX_OUTPUT_CACHE_LOCK:
        hit = _MAX_OUTPUT_CACHE.get(key)
        if hit is not None:
            val, ts = hit
            if val is not None or now - ts < 300:
                return val if val is not None else fallback
    try:
        resolved = _lookup_model_max_output_tokens(model)
        if resolved is None:
            raise LookupError(f"model '{name}' entry has no max output field")
        with _MAX_OUTPUT_CACHE_LOCK:
            _MAX_OUTPUT_CACHE[key] = (resolved, now)
        logger.info("Resolved max output tokens for %s: %d", name, resolved)
        return resolved
    except Exception as e:
        logger.warning(
            "Failed to resolve max output tokens for %s (%s); fallback to %d",
            name,
            e,
            fallback,
        )
        with _MAX_OUTPUT_CACHE_LOCK:
            _MAX_OUTPUT_CACHE[key] = (None, now)
        return fallback


def _extract_content(response_json: Dict[str, Any]) -> str:
    """从 OpenAI 响应中提取文本内容。"""
    try:
        return response_json["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _clean_json(content: str) -> str:
    if content.startswith("```json"):
        content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
    elif content.startswith("```"):
        content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
    return content.strip()


class VLMClient:
    @staticmethod
    def _is_enabled(model: Dict[str, Any]) -> bool:
        return model.get("enabled") is not False

    def __init__(
        self,
        model_name: str = None,
        api_key: str = None,
        models: List[Dict[str, Any]] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.circuit_breaker = circuit_breaker or get_circuit_breaker()

        # 多模型模式
        if models and len(models) > 0:
            self.models = sorted(models, key=lambda m: int(m.get("priority", 99)))
            self.is_local = False
            self._model_name = self.models[0]["name"] if self.models else "None"
            self._strategy = "priority"
            self._round_robin_index = 0
            self._rr_lock = threading.Lock()
            logger.info(f"Initialized multi-model VLMClient with {len(self.models)} models: "
                        f"{[m['name'] for m in self.models]}")
            return

        # 单模型兼容模式
        mn = model_name or "None"
        self._model_name = mn
        self.models = [{"name": mn, "base_url": None, "api_key": api_key, "priority": 1}]
        self.is_local = (mn == "None" or not mn)

        if self.is_local:
            logger.info("Initializing Local VLM (zai-org/GLM-4.6V-Flash)...")
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
                self.tokenizer = AutoTokenizer.from_pretrained("zai-org/GLM-4.6V-Flash", trust_remote_code=True)
                self.model = AutoModelForCausalLM.from_pretrained(
                    "zai-org/GLM-4.6V-Flash",
                    torch_dtype=torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True,
                ).to(self.device).eval()
                logger.info("Local VLM loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load local VLM: {e}")
                raise
        else:
            logger.info(f"Initialized API VLM Client with model: {mn}")

        self._strategy = "priority"
        self._round_robin_index = 0
        self._rr_lock = threading.Lock()

    def annotate_image(
        self,
        image: Image.Image,
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        if self.is_local:
            return self._annotate_local(image)
        from auto_tag.core.config import settings as s
        self._strategy = getattr(s, "vlm_strategy", "round_robin") or "round_robin"
        if self._strategy == "round_robin":
            return self._annotate_with_round_robin(image, profile=profile)
        return self._annotate_with_failover(image, profile=profile)

    def annotate_image_incremental(
        self,
        image: Image.Image,
        existing_labels: Dict[str, Any],
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        from auto_tag.core.config import settings
        keys = [
            k for k in (settings.questions or {}).keys()
            if k not in (existing_labels or {})
        ]
        if not keys:
            return dict(existing_labels or {})
        if self.is_local:
            part = self._annotate_subset_local(image, keys)
        else:
            from auto_tag.core.config import settings as s
            self._strategy = getattr(s, "vlm_strategy", "round_robin") or "round_robin"
            if self._strategy == "round_robin":
                part = self._annotate_subset_with_round_robin(image, keys, profile=profile)
            else:
                part = self._annotate_subset_with_failover(image, keys, profile=profile)
        out = dict(existing_labels or {})
        if isinstance(part, dict):
            out.update(part)
        return out

    # ── Failover 核心逻辑 ──────────────────────────────

    def _annotate_with_failover(
        self,
        image: Image.Image,
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        last_error = ""
        for idx, model in enumerate(self.models):
            if not self._is_enabled(model):
                continue
            endpoint_id = vlm_model_endpoint_id(model, idx)
            if self.circuit_breaker.is_tripped(endpoint_id):
                continue
            try:
                result = self._call_single_model(model, image, profile=profile)
                self.circuit_breaker.record_success(endpoint_id)
                return result
            except Exception as e:
                self.circuit_breaker.record_failure(endpoint_id, str(e))
                last_error = str(e)
                logger.warning(
                    f"Endpoint '{endpoint_id}' (model={model.get('name')}) failed: {e}, trying next..."
                )
        raise AllModelsFailedError(f"All models failed. Last error: {last_error}")

    def _annotate_subset_with_failover(
        self,
        image: Image.Image,
        keys: List[str],
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        last_error = ""
        for idx, model in enumerate(self.models):
            if not self._is_enabled(model):
                continue
            endpoint_id = vlm_model_endpoint_id(model, idx)
            if self.circuit_breaker.is_tripped(endpoint_id):
                continue
            try:
                result = self._call_single_model_subset(
                    model, image, keys, profile=profile
                )
                self.circuit_breaker.record_success(endpoint_id)
                return result
            except Exception as e:
                self.circuit_breaker.record_failure(endpoint_id, str(e))
                last_error = str(e)
                logger.warning(
                    f"Endpoint '{endpoint_id}' (model={model.get('name')}) subset failed: {e}, trying next..."
                )
        raise AllModelsFailedError(f"All models failed for subset. Last error: {last_error}")

    # ── Round-Robin ────────────────────────────────────

    def _get_available_models(self) -> List[tuple[int, Dict[str, Any]]]:
        available: List[tuple[int, Dict[str, Any]]] = []
        for idx, model in enumerate(self.models):
            if not self._is_enabled(model):
                continue
            endpoint_id = vlm_model_endpoint_id(model, idx)
            if not self.circuit_breaker.is_tripped(endpoint_id):
                available.append((idx, model))
        return available

    def _annotate_with_round_robin(
        self,
        image: Image.Image,
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        available = self._get_available_models()
        if not available:
            raise AllModelsFailedError("All models are currently tripped (round_robin).")
        with self._rr_lock:
            pick = available[self._round_robin_index % len(available)]
            self._round_robin_index += 1
        model_idx, model = pick
        endpoint_id = vlm_model_endpoint_id(model, model_idx)
        try:
            result = self._call_single_model(model, image, profile=profile)
            self.circuit_breaker.record_success(endpoint_id)
            return result
        except Exception as e:
            self.circuit_breaker.record_failure(endpoint_id, str(e))
            logger.warning(
                f"Endpoint '{endpoint_id}' (model={model.get('name')}) round_robin failed: {e}, falling back..."
            )
            return self._annotate_with_failover(image, profile=profile)

    def _annotate_subset_with_round_robin(
        self,
        image: Image.Image,
        keys: List[str],
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        available = self._get_available_models()
        if not available:
            raise AllModelsFailedError("All models are currently tripped (round_robin).")
        with self._rr_lock:
            pick = available[self._round_robin_index % len(available)]
            self._round_robin_index += 1
        model_idx, model = pick
        endpoint_id = vlm_model_endpoint_id(model, model_idx)
        try:
            result = self._call_single_model_subset(
                model, image, keys, profile=profile
            )
            self.circuit_breaker.record_success(endpoint_id)
            return result
        except Exception as e:
            self.circuit_breaker.record_failure(endpoint_id, str(e))
            logger.warning(
                f"Endpoint '{endpoint_id}' (model={model.get('name')}) round_robin subset failed: {e}, falling back..."
            )
            return self._annotate_subset_with_failover(image, keys, profile=profile)

    # ── 多轮对话式 API 调用 ─────────────────────────────

    def _messages_with_image(
        self,
        image: Image.Image,
        text: str,
        examples: Optional[List[Tuple[str, str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        """构造首轮消息：examples 为 (维度, 档位值, base64) 参考样图。

        编排原则：跨请求保持相同的内容（prompt 文本 + 参考样图）放消息前部，
        每请求不同的待标注图放最后，使前缀稳定，提升推理侧 prefix/KV cache 命中率。
        """
        content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        if examples:
            content.append(
                {
                    "type": "text",
                    "text": (
                        "Before the image to annotate, the following REFERENCE EXAMPLES "
                        "are provided for calibration, each labeled with the dimension "
                        "and value it demonstrates:"
                    ),
                }
            )
            for qkey, value, b64 in examples:
                content.append(
                    {"type": "text", "text": f"[Reference example: {qkey} = {value}]"}
                )
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    }
                )
            content.append(
                {
                    "type": "text",
                    "text": (
                        "The reference examples above are ONLY for aligning your judgement "
                        "scale with the demonstrated values. The LAST image in this message "
                        "is the one to annotate:"
                    ),
                }
            )
        from auto_tag.core.config import settings as _settings

        main_image = resize_image_for_vlm(
            image, getattr(_settings, "vlm_image_max_side", 0)
        )
        base64_image = encode_pil_image_to_base64(main_image)
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
            },
        )
        return [{"role": "user", "content": content}]

    @staticmethod
    def _parse_json_content(content: str) -> Dict[str, Any]:
        cleaned = _clean_json(content or "")
        if not cleaned:
            raise json.JSONDecodeError("Empty response", content or "", 0)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError(
                "Top-level JSON must be an object", cleaned, 0
            )
        return parsed

    @staticmethod
    def _dump_validation_chain(
        model: Dict[str, Any],
        errors: List[str],
        messages: List[Dict[str, Any]],
        last_parsed: Optional[Dict[str, Any]],
        reason: str,
        finish_reason: str = "",
    ) -> None:
        """校验链路最终失败时，将完整对话链（去除图片 base64）追写到 JSONL。

        正式配置项 vlm_chain_dump 控制开关（默认关）；环境变量 VLM_CHAIN_DUMP
        存在时视为开启并覆盖转储路径（兼容旧用法）。
        """
        try:
            from auto_tag.core.config import settings as _dump_settings

            env_path = os.environ.get("VLM_CHAIN_DUMP", "")
            enabled = bool(getattr(_dump_settings, "vlm_chain_dump", False)) or bool(env_path)
            if not enabled:
                return
            slim = []
            for m in messages:
                c = m.get("content")
                if isinstance(c, list):
                    c = [
                        {"type": "image_url", "image_url": "<base64 omitted>"}
                        if isinstance(p, dict) and p.get("type") == "image_url"
                        else p
                        for p in c
                    ]
                slim.append({"role": m.get("role"), "content": c})
            rec = {
                "ts": __import__("time").time(),
                "reason": reason,
                "finish_reason": finish_reason,
                "model": model.get("name"),
                "endpoint_id": model.get("id") or model.get("endpoint_id"),
                "errors": errors,
                "last_parsed": last_parsed,
                "messages": slim,
            }
            path = env_path or str(
                getattr(
                    _dump_settings,
                    "vlm_chain_dump_path",
                    "",
                )
                or "logs/vlm_validation_chain.jsonl"
            )
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            logger.warning("VLM validation chain dumped to %s", path)
        except Exception as e:
            logger.debug("validation chain dump failed: %s", e)

    @retry(
        stop=stop_after_attempt(4),
        # 指数退让：2s → 4s → 8s（上限 60s），避免等间隔重试加剧服务端压力
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception_type(httpx.HTTPError),
        before_sleep=lambda rs: logger.warning(
            f"VLM API network error, retrying in {rs.next_action.sleep}s..."
        ),
    )
    def _chat_raw(
        self,
        model: Dict[str, Any],
        messages: List[Dict[str, Any]],
        *,
        profile: Optional["PipelineProfile"] = None,
    ) -> Tuple[str, str]:
        """单次 HTTP 往返，仅在网络错误时重试；不在此处解析 JSON。返回 (content, finish_reason)。"""
        import time as _time

        if timing_enabled():
            timing_record(
                "http_start",
                thread=__import__("threading").current_thread().name,
                msg_count=len(messages),
            )
        from auto_tag.core.config import settings as _settings

        http_timeout = float(getattr(_settings, "vlm_http_timeout", 60) or 60)
        http_timeout = max(5.0, min(600.0, http_timeout))
        # thinking 模型的 reasoning 与 content 共用 max_tokens 预算，给足余量防截断；
        # 优先级：模型条目 max_tokens > 全局 vlm_max_tokens > 自动查询模型上限
        model_max_tokens = model.get("max_tokens")
        if model_max_tokens not in (None, ""):
            try:
                model_max_tokens = max(1, min(131072, int(model_max_tokens)))
            except (TypeError, ValueError):
                model_max_tokens = None
        if model_max_tokens:
            max_tokens = model_max_tokens
        else:
            cfg_max_tokens = getattr(_settings, "vlm_max_tokens", None)
            if cfg_max_tokens:
                max_tokens = max(1, min(131072, int(cfg_max_tokens)))
            else:
                max_tokens = resolve_model_max_output_tokens(model)
        t0 = _time.perf_counter()
        thread_name = __import__("threading").current_thread().name
        try:
            resp = openai_chat_completion(
                model=model["name"],
                messages=messages,
                api_key=model.get("api_key"),
                base_url=model.get("base_url"),
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                timeout=http_timeout,
            )
        except Exception as e:
            elapsed = round(_time.perf_counter() - t0, 3)
            logger.warning(
                "VLM HTTP call failed after %.1fs: %s: %s",
                elapsed,
                type(e).__name__,
                str(e)[:300],
            )
            if timing_enabled():
                timing_record(
                    "http_failed",
                    thread=thread_name,
                    elapsed_s=elapsed,
                    error_type=type(e).__name__,
                    error=str(e)[:300],
                    msg_count=len(messages),
                )
            raise
        elapsed = round(_time.perf_counter() - t0, 3)
        content = _extract_content(resp)
        finish_reason = _extract_finish_reason(resp)
        if timing_enabled():
            timing_record(
                "http_done",
                thread=thread_name,
                elapsed_s=elapsed,
                resp_chars=len(content),
            )
        if profile is not None:
            profile.increment("vlm_http_calls")
        return content, finish_reason

    def _annotate_via_conversation(
        self,
        model: Dict[str, Any],
        image: Image.Image,
        initial_prompt: str,
        *,
        keys: Optional[List[str]] = None,
        profile: Optional["PipelineProfile"] = None,
        examples: Optional[List[Tuple[str, str, str]]] = None,
    ) -> Dict[str, Any]:
        """首轮带图提问；JSON/校验失败则在同一会话中文字追问改正（不再重传图片）。"""
        from auto_tag.core.config import settings

        max_corr = max(
            0, int(getattr(settings, "vlm_validation_max_corrections", 2) or 2)
        )
        max_turns = 1 + max_corr
        messages: List[Dict[str, Any]] = self._messages_with_image(
            image, initial_prompt, examples=examples
        )
        last_raw = ""
        last_parsed: Optional[Dict[str, Any]] = None

        for turn in range(max_turns):
            last_raw, finish_reason = self._chat_raw(model, messages, profile=profile)

            if not (last_raw or "").strip():
                logger.warning("VLM returned empty content at turn %d", turn)
                if turn == 0:
                    if timing_enabled():
                        timing_record(
                            "http_empty_failover",
                            thread=__import__("threading").current_thread().name,
                            turn=turn,
                            msg_count=len(messages),
                        )
                    raise EmptyVLMResponseError("VLM returned empty content")
                # turn>0 时空响应：上一轮输出已知非法，不能放行，触发 failover
                raise EmptyVLMResponseError(
                    f"VLM returned empty content at turn {turn}"
                )

            parse_error: Optional[str] = None
            last_parsed = None
            try:
                last_parsed = self._parse_json_content(last_raw)
            except json.JSONDecodeError as e:
                parse_error = str(e)

            echo_prev = True
            if last_parsed is not None:
                validation = self.validate_against_questions(last_parsed, keys=keys)
                if validation["valid"]:
                    if turn > 0:
                        logger.info(
                            "VLM output valid after %d follow-up turn(s)", turn
                        )
                    return last_parsed
                if turn >= max_turns - 1:
                    # 纠正轮数用尽仍非法：抛异常触发 failover/标记失败，绝不落盘非法值
                    logger.warning(
                        "VLM still invalid after %d follow-up turn(s): %s",
                        max_corr,
                        "; ".join(validation["errors"]),
                    )
                    self._dump_validation_chain(
                        model,
                        validation["errors"],
                        messages + [{"role": "assistant", "content": last_raw}],
                        last_parsed,
                        reason="invalid_after_corrections",
                        finish_reason=finish_reason,
                    )
                    raise VLMValidationError(
                        "VLM output still invalid after "
                        f"{max_corr} correction(s): " + "; ".join(validation["errors"])
                    )
                follow_up = self._generate_correction_prompt(
                    last_parsed, validation["errors"], keys=keys
                )
                logger.info(
                    "VLM validation failed (follow-up %d/%d): %s",
                    turn + 1,
                    max_corr,
                    "; ".join(validation["errors"]),
                )
            else:
                truncated = finish_reason == "length"
                if truncated:
                    logger.warning(
                        "VLM response truncated (finish_reason=length, %d chars) at turn %d",
                        len(last_raw or ""),
                        turn,
                    )
                if turn >= max_turns - 1:
                    # 纠正轮数用尽仍无法解析：抛异常触发 failover/标记失败，不返回空 dict
                    logger.warning(
                        "VLM JSON still unparseable after %d follow-up turn(s): %s",
                        max_corr,
                        parse_error,
                    )
                    self._dump_validation_chain(
                        model,
                        [parse_error or "invalid JSON"],
                        messages + [{"role": "assistant", "content": last_raw}],
                        None,
                        reason=(
                            "truncated_after_corrections"
                            if truncated
                            else "unparseable_after_corrections"
                        ),
                        finish_reason=finish_reason,
                    )
                    raise VLMValidationError(
                        f"VLM JSON unparseable after {max_corr} correction(s): "
                        f"{parse_error}"
                    )
                if truncated:
                    # 截断场景：不回显残缺输出，避免模型从断点续写再次被截断；
                    # 改要求紧凑单行 JSON 以降低输出长度
                    follow_up = self._generate_truncation_correction_prompt(keys=keys)
                    echo_prev = False
                else:
                    follow_up = self._generate_json_parse_correction_prompt(
                        last_raw, parse_error or "invalid JSON", keys=keys
                    )
                logger.info(
                    "VLM JSON parse failed (follow-up %d/%d)%s",
                    turn + 1,
                    max_corr,
                    " [truncated]" if truncated else "",
                )

            if echo_prev:
                messages.append({"role": "assistant", "content": last_raw})
            messages.append({"role": "user", "content": follow_up})

        return last_parsed or {}

    def _call_single_model(
        self,
        model: Dict[str, Any],
        image: Image.Image,
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        examples = self._collect_examples(None)
        return self._annotate_via_conversation(
            model,
            image,
            self._generate_prompt(with_examples_note=bool(examples)),
            keys=None,
            profile=profile,
            examples=examples,
        )

    def _call_single_model_subset(
        self,
        model: Dict[str, Any],
        image: Image.Image,
        keys: List[str],
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        examples = self._collect_examples(keys)
        return self._annotate_via_conversation(
            model,
            image,
            self._generate_prompt_for_keys(keys, with_examples_note=bool(examples)),
            keys=keys,
            profile=profile,
            examples=examples,
        )

    # ── 旧单模型 API 调用（保留向后兼容） ──────────────

    def _annotate_api(
        self,
        image: Image.Image,
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        model = self.models[0] if self.models else {}
        examples = self._collect_examples(None)
        return self._annotate_via_conversation(
            model,
            image,
            self._generate_prompt(with_examples_note=bool(examples)),
            keys=None,
            profile=profile,
            examples=examples,
        )

    def _annotate_subset_api(
        self,
        image: Image.Image,
        keys: List[str],
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        model = self.models[0] if self.models else {}
        examples = self._collect_examples(keys)
        return self._annotate_via_conversation(
            model,
            image,
            self._generate_prompt_for_keys(keys, with_examples_note=bool(examples)),
            keys=keys,
            profile=profile,
            examples=examples,
        )

    # ── Prompt 生成 ────────────────────────────────────

    @staticmethod
    def _schema_dict_for_keys(keys: Optional[List[str]] = None) -> Dict[str, Any]:
        from auto_tag.core.config import settings

        qs = dict(settings.questions or {})
        if keys is None:
            return qs
        return {k: qs.get(k, {}) for k in keys if k in qs}

    @classmethod
    def _prompt_schema_dict(cls, keys: Optional[List[str]] = None) -> Dict[str, Any]:
        """写入 prompt 的 schema：examples 文件路径替换为占位说明，避免路径进入模型上下文。"""
        schema = cls._schema_dict_for_keys(keys)
        out: Dict[str, Any] = {}
        for k, details in schema.items():
            if isinstance(details, dict) and details.get("examples"):
                d = dict(details)
                d["examples"] = (
                    "<reference images provided after the main image, "
                    "labeled [Reference example: key = value]>"
                )
                out[k] = d
            else:
                out[k] = details
        return out

    @classmethod
    def _collect_examples(
        cls, keys: Optional[List[str]] = None
    ) -> List[Tuple[str, str, str]]:
        """从 questions 的 examples 字段收集 (维度, 档位值, base64) 参考样图。

        路径支持绝对路径或相对 config.json 的相对路径；加载失败的样图跳过并告警，
        不阻断标注流程。
        """
        from auto_tag.core.config import settings

        max_side = max(
            128, int(getattr(settings, "vlm_example_image_max_side", 512) or 512)
        )
        schema = cls._schema_dict_for_keys(keys)
        out: List[Tuple[str, str, str]] = []
        for qkey, details in schema.items():
            if not isinstance(details, dict):
                continue
            examples = details.get("examples")
            if not isinstance(examples, dict) or not examples:
                continue
            for value in sorted(examples.keys(), key=_example_value_sort_key):
                path = str(examples[value] or "")
                resolved = resolve_example_path(path)
                b64 = load_example_image_base64(resolved, max_side)
                if b64 is None:
                    if resolved not in _EXAMPLE_IMAGE_WARNED:
                        _EXAMPLE_IMAGE_WARNED.add(resolved)
                        logger.warning(
                            "Reference example unavailable: %s=%s (path=%s)",
                            qkey,
                            value,
                            path,
                        )
                    continue
                out.append((qkey, str(value), b64))
        return out

    @staticmethod
    def _example_value_for_question(details: Dict[str, Any]) -> Any:
        """根据 question 定义生成 one-shot 示例值（未知 type 亦给出占位）。"""
        typ = str(details.get("type", "string") or "string")
        choices = details.get("choices") or []

        if typ in ("category", "enum") and choices:
            return choices[0]
        if typ == "enum":
            return "example_value"
        if typ == "int":
            try:
                return int(details.get("min", 0))
            except (TypeError, ValueError):
                return 0
        if typ == "float":
            try:
                return float(details.get("min", 0.0))
            except (TypeError, ValueError):
                return 0.0
        if typ == "string":
            desc = str(details.get("description", "") or "").strip()
            return desc[:48] if desc else "example text"
        return "example"

    @classmethod
    def build_example_json(cls, keys: Optional[List[str]] = None) -> Dict[str, Any]:
        """从 questions 生成完整示例 JSON（用于 one-shot prompt）。"""
        schema = cls._schema_dict_for_keys(keys)
        return {
            key: cls._example_value_for_question(details if isinstance(details, dict) else {})
            for key, details in schema.items()
        }

    def _generate_prompt(self, *, with_examples_note: bool = False) -> str:
        schema_dict = self._prompt_schema_dict()
        schema_json = json.dumps(schema_dict, indent=4, ensure_ascii=False)
        example_json = json.dumps(
            self.build_example_json(), indent=4, ensure_ascii=False
        )
        examples_note = (
            "\n\nSome fields provide reference example images BEFORE the image to annotate "
            "(each labeled [Reference example: key = value]). For those fields, compare "
            "visually against the reference examples and align your scale/judgement with them. "
            "The LAST image in the message is always the one to annotate."
            if with_examples_note
            else ""
        )
        return f"""Please analyze this image and provide a structured JSON output describing it.

You must strictly follow this JSON schema (field definitions):
{schema_json}

Example of a valid response (match this structure — scalar values at top level, no nested objects for numbers):
{example_json}{examples_note}

Return ONLY valid JSON. Do not include explanations or markdown fences."""

    def _generate_prompt_for_keys(
        self, keys: List[str], *, with_examples_note: bool = False
    ) -> str:
        schema_dict = self._prompt_schema_dict(keys)
        schema_json = json.dumps(schema_dict, indent=4, ensure_ascii=False)
        example_json = json.dumps(
            self.build_example_json(keys), indent=4, ensure_ascii=False
        )
        examples_note = (
            "\n\nSome fields provide reference example images BEFORE the image to annotate "
            "(each labeled [Reference example: key = value]). For those fields, compare "
            "visually against the reference examples and align your scale/judgement with them. "
            "The LAST image in the message is always the one to annotate."
            if with_examples_note
            else ""
        )
        return f"""Please analyze this image and provide a structured JSON output.

You must strictly follow this JSON schema (only these keys):
{schema_json}

Example of a valid response:
{example_json}{examples_note}

Return ONLY valid JSON. Do not include markdown fences."""

    def _generate_correction_prompt(
        self,
        current_result: Dict[str, Any],
        errors: List[str],
        *,
        keys: Optional[List[str]] = None,
    ) -> str:
        schema_dict = self._prompt_schema_dict(keys)
        schema_json = json.dumps(schema_dict, indent=4, ensure_ascii=False)
        example_json = json.dumps(
            self.build_example_json(keys), indent=4, ensure_ascii=False
        )
        prev_json = json.dumps(current_result, indent=4, ensure_ascii=False)
        err_lines = "\n".join(f"- {e}" for e in errors)
        return f"""Your previous JSON response did not pass validation against the required schema.

Required schema:
{schema_json}

Example of a valid response:
{example_json}

Your previous output:
{prev_json}

Validation errors:
{err_lines}

Look at our conversation above (the image was in the first message). Fix every error and return ONLY a corrected JSON object.
Use the same top-level keys. Put scalar values directly (e.g. "num_of_person": 2, not nested objects).
Do not include markdown or explanations."""

    def _generate_json_parse_correction_prompt(
        self,
        raw_content: str,
        parse_error: str,
        *,
        keys: Optional[List[str]] = None,
    ) -> str:
        schema_dict = self._prompt_schema_dict(keys)
        schema_json = json.dumps(schema_dict, indent=4, ensure_ascii=False)
        example_json = json.dumps(
            self.build_example_json(keys), indent=4, ensure_ascii=False
        )
        preview = (raw_content or "")[:4000]
        return f"""Your previous response could not be parsed as valid JSON.

Parse error: {parse_error}

Required schema:
{schema_json}

Example of a valid response:
{example_json}

Your previous response:
{preview}

Return ONLY a corrected JSON object. No markdown fences or explanations."""

    # ── 结果校验 ────────────────────────────────────

    @staticmethod
    def validate_against_questions(
        result: Dict[str, Any],
        *,
        keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """校验 VLM 返回结果是否符合 questions schema。

        对每个 question 字段，若该 question 有已知类型约束则做校验；
        无 choices 的 enum / 未知 type 仅要求 key 存在（便于测试非规范 schema）。
        返回：{"valid": bool, "errors": List[str]}
        """
        from auto_tag.core.config import settings

        errors: List[str] = []
        qs = settings.questions or {}
        check_keys = list(keys) if keys is not None else list(qs.keys())

        for key in check_keys:
            details = qs.get(key)
            if not isinstance(details, dict):
                continue

            if key not in result:
                errors.append(f"Missing key: {key}")
                continue

            val = result[key]
            typ = str(details.get("type", "") or "")

            if typ in ("category", "enum"):
                choices = details.get("choices") or []
                if choices and val not in choices:
                    errors.append(
                        f"Key '{key}': value '{val}' not in choices {choices}"
                    )

            elif typ == "int":
                if isinstance(val, bool) or not isinstance(val, int):
                    errors.append(
                        f"Key '{key}': expected int, got {type(val).__name__} ('{val}')"
                    )

            elif typ == "float":
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    errors.append(
                        f"Key '{key}': expected number, got {type(val).__name__} ('{val}')"
                    )

            # string / enum 无 choices / 未知 type → 仅要求 key 存在

        return {"valid": len(errors) == 0, "errors": errors}


    # ── 本地模型 ────────────────────────────────────────

    def _local_infer_json(self, image: Image.Image, prompt: str) -> Dict[str, Any]:
        import torch

        image = image.convert("RGB")
        inputs = self.tokenizer.apply_chat_template(
            [{"role": "user", "image": image, "content": prompt}],
            add_generation_prompt=True,
            tokenize=True,
            return_tensors="pt",
            return_dict=True,
        )
        inputs = inputs.to(self.device)
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs, max_new_tokens=1024, do_sample=True, temperature=0.8
            )
            outputs = outputs[:, inputs["input_ids"].shape[1] :]
            content = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        content = _clean_json(content)
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("Top-level JSON must be an object", content, 0)
        return parsed

    def _correct_until_valid_local(
        self,
        image: Image.Image,
        result: Dict[str, Any],
        *,
        keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from auto_tag.core.config import settings

        max_corr = max(
            0, int(getattr(settings, "vlm_validation_max_corrections", 2) or 2)
        )
        current = dict(result) if isinstance(result, dict) else {}

        for attempt in range(max_corr + 1):
            validation = self.validate_against_questions(current, keys=keys)
            if validation["valid"]:
                return current
            if attempt >= max_corr:
                logger.warning(
                    "Local VLM output still invalid after %d correction(s): %s",
                    max_corr,
                    "; ".join(validation["errors"]),
                )
                raise VLMValidationError(
                    "Local VLM output still invalid after "
                    f"{max_corr} correction(s): " + "; ".join(validation["errors"])
                )
            prompt = self._generate_correction_prompt(
                current, validation["errors"], keys=keys
            )
            try:
                current = self._local_infer_json(image, prompt)
            except Exception as e:
                logger.warning("Local VLM correction failed: %s", e)
                return current
        return current

    def _annotate_local(self, image: Image.Image) -> Dict[str, Any]:
        logger.debug("Requesting local VLM annotation...")
        try:
            result = self._local_infer_json(image, self._generate_prompt())
            result = self._correct_until_valid_local(image, result, keys=None)
            logger.info("Successfully generated local annotation")
            return result
        except json.JSONDecodeError as e:
            logger.error("Local VLM returned invalid JSON: %s", e)
            raise Exception(f"Invalid JSON from Local VLM: {e}") from e
        except Exception as e:
            logger.error(f"Local VLM inference error: {e}")
            raise

    def _annotate_subset_local(self, image: Image.Image, keys: List[str]) -> Dict[str, Any]:
        logger.debug("Requesting local VLM incremental annotation...")
        try:
            result = self._local_infer_json(image, self._generate_prompt_for_keys(keys))
            return self._correct_until_valid_local(image, result, keys=keys)
        except Exception as e:
            logger.error(f"Local VLM subset inference error: {e}")
            raise


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.DEBUG)
    client = VLMClient(model_name="test-model", api_key="test-key")
    print("\n--- Generated Prompt ---")
    print(client._generate_prompt())
    print("------------------------\n")
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_img = os.path.join(base_dir, "test", "test_data", "test.bmp")
    if os.path.exists(test_img):
        img = Image.open(test_img)
        b64 = encode_pil_image_to_base64(img)
        print(f"Base64 encoding successful, length: {len(b64)}")