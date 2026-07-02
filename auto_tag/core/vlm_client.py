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
from typing import Any, Dict, List, Optional

from PIL import Image

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from auto_tag.core.circuit_breaker import CircuitBreaker, get_circuit_breaker

logger = logging.getLogger(__name__)


def encode_pil_image_to_base64(image: Image.Image) -> str:
    buffered = io.BytesIO()
    image.convert('RGB').save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


class AllModelsFailedError(Exception):
    """所有模型均失败时抛出。"""
    pass


def openai_chat_completion(
    model: str,
    messages: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None,
    max_tokens: int = 4096,
    timeout: float = 120.0,
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

    def annotate_image(self, image: Image.Image) -> Dict[str, Any]:
        if self.is_local:
            return self._annotate_local(image)
        from auto_tag.core.config import settings as s
        self._strategy = getattr(s, "vlm_strategy", "priority") or "priority"
        if self._strategy == "round_robin":
            return self._annotate_with_round_robin(image)
        return self._annotate_with_failover(image)

    def annotate_image_incremental(self, image: Image.Image, existing_labels: Dict[str, Any]) -> Dict[str, Any]:
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
            self._strategy = getattr(s, "vlm_strategy", "priority") or "priority"
            if self._strategy == "round_robin":
                part = self._annotate_subset_with_round_robin(image, keys)
            else:
                part = self._annotate_subset_with_failover(image, keys)
        out = dict(existing_labels or {})
        if isinstance(part, dict):
            out.update(part)
        return out

    # ── Failover 核心逻辑 ──────────────────────────────

    def _annotate_with_failover(self, image: Image.Image) -> Dict[str, Any]:
        last_error = ""
        for model in self.models:
            name = model["name"]
            if self.circuit_breaker.is_tripped(name):
                continue
            try:
                result = self._call_single_model(model, image)
                self.circuit_breaker.record_success(name)
                return result
            except Exception as e:
                self.circuit_breaker.record_failure(name, str(e))
                last_error = str(e)
                logger.warning(f"Model '{name}' failed: {e}, trying next...")
        raise AllModelsFailedError(f"All models failed. Last error: {last_error}")

    def _annotate_subset_with_failover(self, image: Image.Image, keys: List[str]) -> Dict[str, Any]:
        last_error = ""
        for model in self.models:
            name = model["name"]
            if self.circuit_breaker.is_tripped(name):
                continue
            try:
                result = self._call_single_model_subset(model, image, keys)
                self.circuit_breaker.record_success(name)
                return result
            except Exception as e:
                self.circuit_breaker.record_failure(name, str(e))
                last_error = str(e)
                logger.warning(f"Model '{name}' subset failed: {e}, trying next...")
        raise AllModelsFailedError(f"All models failed for subset. Last error: {last_error}")

    # ── Round-Robin ────────────────────────────────────

    def _get_available_models(self) -> List[Dict[str, Any]]:
        return [m for m in self.models if not self.circuit_breaker.is_tripped(m["name"])]

    def _annotate_with_round_robin(self, image: Image.Image) -> Dict[str, Any]:
        available = self._get_available_models()
        if not available:
            raise AllModelsFailedError("All models are currently tripped (round_robin).")
        idx = self._round_robin_index % len(available)
        self._round_robin_index += 1
        model = available[idx]
        try:
            result = self._call_single_model(model, image)
            self.circuit_breaker.record_success(model["name"])
            return result
        except Exception as e:
            self.circuit_breaker.record_failure(model["name"], str(e))
            logger.warning(f"Model '{model['name']}' round_robin failed: {e}, falling back...")
            return self._annotate_with_failover(image)

    def _annotate_subset_with_round_robin(self, image: Image.Image, keys: List[str]) -> Dict[str, Any]:
        available = self._get_available_models()
        if not available:
            raise AllModelsFailedError("All models are currently tripped (round_robin).")
        idx = self._round_robin_index % len(available)
        self._round_robin_index += 1
        model = available[idx]
        try:
            result = self._call_single_model_subset(model, image, keys)
            self.circuit_breaker.record_success(model["name"])
            return result
        except Exception as e:
            self.circuit_breaker.record_failure(model["name"], str(e))
            logger.warning(f"Model '{model['name']}' round_robin subset failed: {e}, falling back...")
            return self._annotate_subset_with_failover(image, keys)

    # ── 实际的 API 调用（含 tenacity 重试） ─────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, json.JSONDecodeError, KeyError)),
        before_sleep=lambda rs: logger.warning(f"VLM API call retrying in {rs.next_action.sleep}s..."),
    )
    def _call_single_model(self, model: Dict[str, Any], image: Image.Image) -> Dict[str, Any]:
        prompt = self._generate_prompt()
        base64_image = encode_pil_image_to_base64(image)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                ],
            }
        ]
        resp = openai_chat_completion(
            model=model["name"],
            messages=messages,
            api_key=model.get("api_key"),
            base_url=model.get("base_url"),
            response_format={"type": "json_object"},
        )
        content = _extract_content(resp)
        content = _clean_json(content)
        return json.loads(content)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, json.JSONDecodeError, KeyError)),
        before_sleep=lambda rs: logger.warning(f"VLM API subset retrying in {rs.next_action.sleep}s..."),
    )
    def _call_single_model_subset(self, model: Dict[str, Any], image: Image.Image, keys: List[str]) -> Dict[str, Any]:
        prompt = self._generate_prompt_for_keys(keys)
        base64_image = encode_pil_image_to_base64(image)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                ],
            }
        ]
        resp = openai_chat_completion(
            model=model["name"],
            messages=messages,
            api_key=model.get("api_key"),
            base_url=model.get("base_url"),
            response_format={"type": "json_object"},
        )
        content = _extract_content(resp)
        content = _clean_json(content)
        return json.loads(content)

    # ── 旧单模型 API 调用（保留向后兼容） ──────────────

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda rs: logger.warning(f"VLM API call failed, retrying in {rs.next_action.sleep}s..."),
    )
    def _annotate_api(self, image: Image.Image) -> Dict[str, Any]:
        prompt = self._generate_prompt()
        base64_image = encode_pil_image_to_base64(image)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                ],
            }
        ]
        model = self.models[0] if self.models else {}
        resp = openai_chat_completion(
            model=self._model_name,
            messages=messages,
            api_key=model.get("api_key"),
            base_url=model.get("base_url"),
            response_format={"type": "json_object"},
        )
        content = _extract_content(resp)
        content = _clean_json(content)
        return json.loads(content)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda rs: logger.warning(f"VLM API subset call failed, retrying in {rs.next_action.sleep}s..."),
    )
    def _annotate_subset_api(self, image: Image.Image, keys: List[str]) -> Dict[str, Any]:
        prompt = self._generate_prompt_for_keys(keys)
        base64_image = encode_pil_image_to_base64(image)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                ],
            }
        ]
        model = self.models[0] if self.models else {}
        resp = openai_chat_completion(
            model=self._model_name,
            messages=messages,
            api_key=model.get("api_key"),
            base_url=model.get("base_url"),
            response_format={"type": "json_object"},
        )
        content = _extract_content(resp)
        content = _clean_json(content)
        return json.loads(content)

    # ── Prompt 生成 ────────────────────────────────────

    def _generate_prompt(self) -> str:
        from auto_tag.core.config import settings
        schema_dict = dict(settings.questions)  # 全字段序列化
        schema_json = json.dumps(schema_dict, indent=4)
        return f"""Please analyze this image and provide a structured JSON output describing it.
You must strictly follow this JSON schema:
{schema_json}
Return ONLY valid JSON format. Do not include any explanations or markdown formatting outside of the JSON block."""

    def _generate_prompt_for_keys(self, keys: List[str]) -> str:
        from auto_tag.core.config import settings
        qs = settings.questions or {}
        schema_dict = {k: qs.get(k, {}) for k in keys}  # 全字段序列化（仅含指定 keys）
        schema_json = json.dumps(schema_dict, indent=4)
        return f"""Please analyze this image and provide a structured JSON output.
You must strictly follow this JSON schema (only these keys):
{schema_json}
Return ONLY valid JSON. Do not include markdown fences."""

    # ── 结果校验 ────────────────────────────────────

    @staticmethod
    def validate_against_questions(result: Dict[str, Any]) -> Dict[str, Any]:
        """校验 VLM 返回结果是否符合 questions schema。

        对每个 question 字段，若该 question 有已知类型约束则做校验；
        无类型定义（自由形式）的 question 不做校验。
        返回：{"valid": bool, "errors": List[str]}
        """
        from auto_tag.core.config import settings

        errors: List[str] = []
        qs = settings.questions or {}

        for key, details in qs.items():
            if key not in result:
                errors.append(f"Missing key: {key}")
                continue

            val = result[key]
            typ = details.get("type", "")

            if typ == "category":
                choices = details.get("choices", [])
                if choices and val not in choices:
                    errors.append(
                        f"Key '{key}': value '{val}' not in choices {choices}"
                    )

            elif typ == "int":
                if not isinstance(val, int):
                    errors.append(
                        f"Key '{key}': expected int, got {type(val).__name__} ('{val}')"
                    )

            elif typ == "float":
                if not isinstance(val, (int, float)):
                    errors.append(
                        f"Key '{key}': expected number, got {type(val).__name__} ('{val}')"
                    )

            # string / 未知 type → 不做形式校验，跳过

        return {"valid": len(errors) == 0, "errors": errors}


    # ── 本地模型 ────────────────────────────────────────

    def _annotate_local(self, image: Image.Image) -> Dict[str, Any]:
        logger.debug("Requesting local VLM annotation...")
        prompt = self._generate_prompt()
        try:
            import torch
            image = image.convert('RGB')
            inputs = self.tokenizer.apply_chat_template(
                [{"role": "user", "image": image, "content": prompt}],
                add_generation_prompt=True,
                tokenize=True,
                return_tensors="pt",
                return_dict=True,
            )
            inputs = inputs.to(self.device)
            with torch.no_grad():
                outputs = self.model.generate(**inputs, max_new_tokens=1024, do_sample=True, temperature=0.8)
                outputs = outputs[:, inputs['input_ids'].shape[1]:]
                content = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            content = _clean_json(content)
            result_json = json.loads(content)
            logger.info("Successfully generated local annotation")
            return result_json
        except json.JSONDecodeError as e:
            logger.error(f"Local VLM returned invalid JSON. Content: {content}")
            raise Exception(f"Invalid JSON from Local VLM: {e}")
        except Exception as e:
            logger.error(f"Local VLM inference error: {e}")
            raise

    def _annotate_subset_local(self, image: Image.Image, keys: List[str]) -> Dict[str, Any]:
        logger.debug("Requesting local VLM incremental annotation...")
        prompt = self._generate_prompt_for_keys(keys)
        try:
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
                outputs = self.model.generate(**inputs, max_new_tokens=1024, do_sample=True, temperature=0.8)
                outputs = outputs[:, inputs["input_ids"].shape[1]:]
                content = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            content = _clean_json(content)
            return json.loads(content)
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