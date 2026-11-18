"""未显式配置时，max_tokens 取模型输出上限的 1/4。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from auto_tag.core.vlm_client import resolve_max_tokens_for_model


class TestResolveMaxTokens(unittest.TestCase):
    def test_auto_ceiling_is_quartered(self) -> None:
        model = {"name": "demo", "max_tokens": None}
        with patch(
            "auto_tag.core.vlm_client.resolve_model_max_output_tokens",
            return_value=65536,
        ):
            self.assertEqual(resolve_max_tokens_for_model(model), 16384)

    def test_explicit_model_value_is_not_quartered(self) -> None:
        model = {"name": "demo", "max_tokens": 1000}
        with patch(
            "auto_tag.core.vlm_client.resolve_model_max_output_tokens",
            side_effect=AssertionError("should not query"),
        ):
            self.assertEqual(resolve_max_tokens_for_model(model), 1000)


if __name__ == "__main__":
    unittest.main()
