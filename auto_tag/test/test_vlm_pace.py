"""开工并发坡度，以及商汤 POST 间隔、429 冷却、图片检查只重试一次。"""
from __future__ import annotations

import unittest
from unittest import mock

import httpx

from auto_tag.core.vlm_annotation_pool import ConcurrencyRamp
from auto_tag.core.vlm_client import (
    VLMClient,
    VLMNoFailoverError,
    _is_inspection_internal,
    _is_sensenova_model,
    _retry_after_seconds,
    reset_vlm_post_pacer_for_tests,
)


class TestConcurrencyRamp(unittest.TestCase):
    def test_opens_one_slot_per_success_until_the_cap(self) -> None:
        ramp = ConcurrencyRamp(3)
        ramp.acquire()
        self.assertEqual(ramp.release(True), 2)
        ramp.acquire()
        self.assertEqual(ramp.release(True), 3)
        ramp.acquire()
        self.assertEqual(ramp.release(True), 3)

    def test_failure_does_not_open_another_slot(self) -> None:
        ramp = ConcurrencyRamp(4)
        ramp.acquire()
        self.assertEqual(ramp.release(False), 1)


def _advancing_clock():
    state = {"t": 0.0}

    def now() -> float:
        return state["t"]

    def sleep(delay: float) -> None:
        state["t"] += float(delay)

    return now, sleep


class TestSenseNovaPace(unittest.TestCase):
    def test_identifies_inspection_and_retry_after(self) -> None:
        self.assertTrue(
            _is_inspection_internal(
                "image call nova inspection failed rpc error: code = Internal"
            )
        )
        self.assertFalse(_is_inspection_internal("image num 112 exceeds limit 50"))
        self.assertTrue(_is_sensenova_model({"base_url": "https://token.sensenova.cn/v1"}))
        request = httpx.Request("POST", "https://token.sensenova.cn/v1/chat/completions")
        response = httpx.Response(429, headers={"retry-after": "12"}, request=request)
        exc = httpx.HTTPStatusError("429", request=request, response=response)
        self.assertEqual(_retry_after_seconds(exc), 12.0)

    def test_429_retries_once_then_stops_without_sleeping_the_suite(self) -> None:
        reset_vlm_post_pacer_for_tests()
        request = httpx.Request("POST", "https://token.sensenova.cn/v1/chat/completions")
        response = httpx.Response(
            429,
            headers={"retry-after": "1"},
            request=request,
            text='{"error":{"message":"inference exceeds tpm/rpm limit"}}',
        )
        calls = {"n": 0}

        def boom(**_kwargs):
            calls["n"] += 1
            raise httpx.HTTPStatusError("429", request=request, response=response)

        now, sleep = _advancing_clock()
        client = VLMClient.__new__(VLMClient)
        with mock.patch("auto_tag.core.vlm_client.openai_chat_completion", boom), \
                mock.patch("auto_tag.core.vlm_client._pace_settings", return_value=(0.0, 20.0, 15.0)), \
                mock.patch("auto_tag.core.vlm_client.resolve_max_tokens_for_model", return_value=16), \
                mock.patch("time.monotonic", now), \
                mock.patch("time.sleep", sleep):
            with self.assertRaises(VLMNoFailoverError):
                client._chat_raw(
                    {"name": "sensenova-6.8-flash-lite", "base_url": "https://token.sensenova.cn/v1"},
                    [],
                )
        self.assertEqual(calls["n"], 2)

    def test_inspection_internal_retries_once(self) -> None:
        reset_vlm_post_pacer_for_tests()
        request = httpx.Request("POST", "https://token.sensenova.cn/v1/chat/completions")
        response = httpx.Response(
            400,
            request=request,
            text='{"error":{"message":"image call nova inspection failed rpc error: code = Internal"}}',
        )
        calls = {"n": 0}

        def boom(**_kwargs):
            calls["n"] += 1
            raise httpx.HTTPStatusError(
                "image call nova inspection failed rpc error: code = Internal",
                request=request,
                response=response,
            )

        now, sleep = _advancing_clock()
        client = VLMClient.__new__(VLMClient)
        with mock.patch("auto_tag.core.vlm_client.openai_chat_completion", boom), \
                mock.patch("auto_tag.core.vlm_client._pace_settings", return_value=(0.0, 20.0, 15.0)), \
                mock.patch("auto_tag.core.vlm_client.resolve_max_tokens_for_model", return_value=16), \
                mock.patch("time.monotonic", now), \
                mock.patch("time.sleep", sleep):
            with self.assertRaises(VLMNoFailoverError):
                client._chat_raw(
                    {"name": "sensenova-6.8-flash-lite", "base_url": "https://token.sensenova.cn/v1"},
                    [],
                )
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
