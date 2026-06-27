import base64
import io
import json
import logging
from typing import Any, Dict, List
from PIL import Image

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
logger = logging.getLogger(__name__)


def encode_pil_image_to_base64(image: Image.Image) -> str:
    """将 PIL 图片对象编码为 base64 字符串"""
    buffered = io.BytesIO()
    # 统一转换为 RGB 并保存为 JPEG 格式进行编码
    image.convert('RGB').save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


class VLMClient:
    def __init__(self, model_name: str, api_key: str = None):
        """
        初始化 VLM 客户端。支持 API 调用和本地推理。
        """
        self.model_name = model_name
        self.api_key = api_key
        self.is_local = (model_name == "None" or not model_name)

        if self.is_local:
            logger.info("Initializing Local VLM (zai-org/GLM-4.6V-Flash)...")
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
                self.tokenizer = AutoTokenizer.from_pretrained("zai-org/GLM-4.6V-Flash", trust_remote_code=True)
                # 使用 bfloat16 或 float16 降低显存占用
                self.model = AutoModelForCausalLM.from_pretrained(
                    "zai-org/GLM-4.6V-Flash",
                    torch_dtype=torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True
                ).to(self.device).eval()
                logger.info("Local VLM loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load local VLM: {e}")
                raise
        else:
            litellm.set_verbose = False
            logger.info(f"Initialized API VLM Client with model: {model_name}")

    def annotate_image(self, image: Image.Image) -> Dict[str, Any]:
        """
        调用 VLM 获取图片的结构化标签 (JSON)。
        """
        if self.is_local:
            return self._annotate_local(image)
        else:
            return self._annotate_api(image)

    def _generate_prompt(self) -> str:
        from auto_tag.core.config import settings

        schema_dict = {}
        for key, details in settings.questions.items():
            if details.get("type") == "category" and "choices" in details:
                schema_dict[key] = f"{details.get('description', '')} (choices: {'/'.join(details['choices'])})"
            elif details.get("type") in ["int", "float"]:
                min_val = details.get("min", "none")
                max_val = details.get("max", "none")
                schema_dict[key] = f"{details.get('description', '')} (min: {min_val}, max: {max_val})"
            else:
                schema_dict[key] = details.get("description", "string")

        schema_json = json.dumps(schema_dict, indent=4)

        prompt = f"""
        Please analyze this image and provide a structured JSON output describing it.
        You must strictly follow this JSON schema:
        {schema_json}
        Return ONLY valid JSON format. Do not include any explanations or markdown formatting outside of the JSON block.
        """
        return prompt

    def _generate_prompt_for_keys(self, keys: List[str]) -> str:
        from auto_tag.core.config import settings

        schema_dict: Dict[str, Any] = {}
        for key in keys:
            details = (settings.questions or {}).get(key) or {}
            if details.get("type") == "category" and "choices" in details:
                schema_dict[key] = (
                    f"{details.get('description', '')} (choices: {'/'.join(details['choices'])})"
                )
            elif details.get("type") in ["int", "float"]:
                min_val = details.get("min", "none")
                max_val = details.get("max", "none")
                schema_dict[key] = (
                    f"{details.get('description', '')} (min: {min_val}, max: {max_val})"
                )
            else:
                schema_dict[key] = details.get("description", "string")

        schema_json = json.dumps(schema_dict, indent=4)
        return f"""
        Please analyze this image and provide a structured JSON output.
        You must strictly follow this JSON schema (only these keys):
        {schema_json}
        Return ONLY valid JSON. Do not include markdown fences.
        """

    def annotate_image_incremental(
        self, image: Image.Image, existing_labels: Dict[str, Any]
    ) -> Dict[str, Any]:
        """仅为 questions 中尚未出现在 existing_labels 的键调用 VLM，再与已有字典合并。"""
        from auto_tag.core.config import settings

        keys = [
            k
            for k in (settings.questions or {}).keys()
            if k not in (existing_labels or {})
        ]
        if not keys:
            return dict(existing_labels or {})
        if self.is_local:
            part = self._annotate_subset_local(image, keys)
        else:
            part = self._annotate_subset_api(image, keys)
        out = dict(existing_labels or {})
        if isinstance(part, dict):
            out.update(part)
        return out

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
                outputs = self.model.generate(
                    **inputs, max_new_tokens=1024, do_sample=True, temperature=0.8
                )
                outputs = outputs[:, inputs["input_ids"].shape[1] :]
                content = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:-3].strip()
            elif content.startswith("```"):
                content = content[3:-3].strip()
            return json.loads(content)
        except Exception as e:
            logger.error(f"Local VLM subset inference error: {e}")
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(
            f"VLM API subset call failed, retrying in {retry_state.next_action.sleep}s..."
        ),
    )
    def _annotate_subset_api(self, image: Image.Image, keys: List[str]) -> Dict[str, Any]:
        prompt = self._generate_prompt_for_keys(keys)
        base64_image = encode_pil_image_to_base64(image)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            }
        ]
        response = litellm.completion(
            model=self.model_name,
            messages=messages,
            api_key=self.api_key,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content[7:-3].strip()
        elif content.startswith("```"):
            content = content[3:-3].strip()
        return json.loads(content)

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
                return_dict=True
            )
            inputs = inputs.to(self.device)

            with torch.no_grad():
                outputs = self.model.generate(**inputs, max_new_tokens=1024, do_sample=True, temperature=0.8)
                outputs = outputs[:, inputs['input_ids'].shape[1]:]
                content = self.tokenizer.decode(outputs[0], skip_special_tokens=True)

            content = content.strip()
            if content.startswith("```json"):
                content = content[7:-3].strip()
            elif content.startswith("```"):
                content = content[3:-3].strip()

            result_json = json.loads(content)
            logger.info("Successfully generated local annotation")
            return result_json

        except json.JSONDecodeError as e:
            logger.error(f"Local VLM returned invalid JSON. Content: {content}")
            raise Exception(f"Invalid JSON from Local VLM: {e}")
        except Exception as e:
            logger.error(f"Local VLM inference error: {e}")
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(
            f"VLM API call failed, retrying in {retry_state.next_action.sleep}s...")
    )
    def _annotate_api(self, image: Image.Image) -> Dict[str, Any]:
        """
        通过 API 调用 VLM 获取图片的结构化标签，含指数退避重试。
        """
        logger.debug("Requesting API VLM annotation...")

        prompt = self._generate_prompt()

        try:
            base64_image = encode_pil_image_to_base64(image)
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                        }
                    ]
                }
            ]

            response = litellm.completion(
                model=self.model_name,
                messages=messages,
                api_key=self.api_key,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content

            content = content.strip()
            if content.startswith("```json"):
                content = content[7:-3].strip()
            elif content.startswith("```"):
                content = content[3:-3].strip()

            result_json = json.loads(content)
            logger.info("Successfully generated API annotation")
            return result_json

        except json.JSONDecodeError as e:
            logger.error(f"VLM API returned invalid JSON. Content: {content}")
            raise Exception(f"Invalid JSON from VLM API: {e}")
        except Exception as e:
            logger.error(f"VLM API call error: {e}")
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
