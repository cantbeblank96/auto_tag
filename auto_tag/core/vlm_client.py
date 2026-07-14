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
import threading
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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


class AllModelsFailedError(Exception):
    """所有模型均失败时抛出。"""
    pass


class EmptyVLMResponseError(Exception):
    """VLM HTTP 200 但 content 为空。"""
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
        resp.raise_for_status()
        return resp.json()


def _build_chat_url(base_url: Optional[str]) -> str:
    if not base_url:
        return "https://api.openai.com/v1/chat/completions"
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    if not url.endswith("/chat/completions"):
        return f"{url}/chat/completions"
    return url


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

    def _messages_with_image(self, image: Image.Image, text: str) -> List[Dict[str, Any]]:
        base64_image = encode_pil_image_to_base64(image)
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            }
        ]

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
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
    ) -> str:
        """单次 HTTP 往返，仅在网络错误时重试；不在此处解析 JSON。"""
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
        t0 = _time.perf_counter()
        thread_name = __import__("threading").current_thread().name
        try:
            resp = openai_chat_completion(
                model=model["name"],
                messages=messages,
                api_key=model.get("api_key"),
                base_url=model.get("base_url"),
                response_format={"type": "json_object"},
                timeout=http_timeout,
            )
        except Exception as e:
            elapsed = round(_time.perf_counter() - t0, 3)
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
        if timing_enabled():
            timing_record(
                "http_done",
                thread=thread_name,
                elapsed_s=elapsed,
                resp_chars=len(content),
            )
        if profile is not None:
            profile.increment("vlm_http_calls")
        return content

    def _annotate_via_conversation(
        self,
        model: Dict[str, Any],
        image: Image.Image,
        initial_prompt: str,
        *,
        keys: Optional[List[str]] = None,
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        """首轮带图提问；JSON/校验失败则在同一会话中文字追问改正（不再重传图片）。"""
        from auto_tag.core.config import settings

        max_corr = max(
            0, int(getattr(settings, "vlm_validation_max_corrections", 2) or 2)
        )
        max_turns = 1 + max_corr
        messages: List[Dict[str, Any]] = self._messages_with_image(
            image, initial_prompt
        )
        last_raw = ""
        last_parsed: Optional[Dict[str, Any]] = None

        for turn in range(max_turns):
            last_raw = self._chat_raw(model, messages, profile=profile)

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
                return last_parsed or {}

            parse_error: Optional[str] = None
            last_parsed = None
            try:
                last_parsed = self._parse_json_content(last_raw)
            except json.JSONDecodeError as e:
                parse_error = str(e)

            if last_parsed is not None:
                validation = self.validate_against_questions(last_parsed, keys=keys)
                if validation["valid"]:
                    if turn > 0:
                        logger.info(
                            "VLM output valid after %d follow-up turn(s)", turn
                        )
                    return last_parsed
                if turn >= max_turns - 1:
                    logger.warning(
                        "VLM still invalid after %d follow-up turn(s): %s",
                        max_corr,
                        "; ".join(validation["errors"]),
                    )
                    return last_parsed
                follow_up = self._generate_correction_prompt(
                    last_parsed, validation["errors"], keys=keys
                )
                logger.info(
                    "VLM validation failed (follow-up %d/%d)",
                    turn + 1,
                    max_corr,
                )
            else:
                if turn >= max_turns - 1:
                    logger.warning(
                        "VLM JSON still unparseable after %d follow-up turn(s): %s",
                        max_corr,
                        parse_error,
                    )
                    return {}
                follow_up = self._generate_json_parse_correction_prompt(
                    last_raw, parse_error or "invalid JSON", keys=keys
                )
                logger.info(
                    "VLM JSON parse failed (follow-up %d/%d)",
                    turn + 1,
                    max_corr,
                )

            messages.append({"role": "assistant", "content": last_raw})
            messages.append({"role": "user", "content": follow_up})

        return last_parsed or {}

    def _call_single_model(
        self,
        model: Dict[str, Any],
        image: Image.Image,
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        return self._annotate_via_conversation(
            model,
            image,
            self._generate_prompt(),
            keys=None,
            profile=profile,
        )

    def _call_single_model_subset(
        self,
        model: Dict[str, Any],
        image: Image.Image,
        keys: List[str],
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        return self._annotate_via_conversation(
            model,
            image,
            self._generate_prompt_for_keys(keys),
            keys=keys,
            profile=profile,
        )

    # ── 旧单模型 API 调用（保留向后兼容） ──────────────

    def _annotate_api(
        self,
        image: Image.Image,
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        model = self.models[0] if self.models else {}
        return self._annotate_via_conversation(
            model,
            image,
            self._generate_prompt(),
            keys=None,
            profile=profile,
        )

    def _annotate_subset_api(
        self,
        image: Image.Image,
        keys: List[str],
        profile: Optional["PipelineProfile"] = None,
    ) -> Dict[str, Any]:
        model = self.models[0] if self.models else {}
        return self._annotate_via_conversation(
            model,
            image,
            self._generate_prompt_for_keys(keys),
            keys=keys,
            profile=profile,
        )

    # ── Prompt 生成 ────────────────────────────────────

    @staticmethod
    def _schema_dict_for_keys(keys: Optional[List[str]] = None) -> Dict[str, Any]:
        from auto_tag.core.config import settings

        qs = dict(settings.questions or {})
        if keys is None:
            return qs
        return {k: qs.get(k, {}) for k in keys if k in qs}

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

    def _generate_prompt(self) -> str:
        schema_dict = self._schema_dict_for_keys()
        schema_json = json.dumps(schema_dict, indent=4, ensure_ascii=False)
        example_json = json.dumps(
            self.build_example_json(), indent=4, ensure_ascii=False
        )
        return f"""Please analyze this image and provide a structured JSON output describing it.

You must strictly follow this JSON schema (field definitions):
{schema_json}

Example of a valid response (match this structure — scalar values at top level, no nested objects for numbers):
{example_json}

Return ONLY valid JSON. Do not include explanations or markdown fences."""

    def _generate_prompt_for_keys(self, keys: List[str]) -> str:
        schema_dict = self._schema_dict_for_keys(keys)
        schema_json = json.dumps(schema_dict, indent=4, ensure_ascii=False)
        example_json = json.dumps(
            self.build_example_json(keys), indent=4, ensure_ascii=False
        )
        return f"""Please analyze this image and provide a structured JSON output.

You must strictly follow this JSON schema (only these keys):
{schema_json}

Example of a valid response:
{example_json}

Return ONLY valid JSON. Do not include markdown fences."""

    def _generate_correction_prompt(
        self,
        current_result: Dict[str, Any],
        errors: List[str],
        *,
        keys: Optional[List[str]] = None,
    ) -> str:
        schema_dict = self._schema_dict_for_keys(keys)
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
        schema_dict = self._schema_dict_for_keys(keys)
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
                return current
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