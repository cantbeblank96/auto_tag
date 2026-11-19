"""参考图：EXIF 转正、工具用原尺寸、检测失败时换下一张候选。"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageOps

from auto_tag.core.vlm_client import (
    VLMClient,
    load_example_image_pil,
    reference_detection_ok,
    reference_payload_for_vlm,
)


def _save_marked_jpeg(path: str, orientation: int) -> None:
    image = Image.new("RGB", (30, 10), (20, 30, 40))
    image.putpixel((0, 0), (255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = orientation
    image.save(path, format="JPEG", quality=95, exif=exif)


class TestReferenceDetection(unittest.TestCase):
    def test_pose_angles_count_as_success(self) -> None:
        self.assertTrue(
            reference_detection_ok({"head_pose": {"yaw": 1.0, "pitch": 2.0, "roll": 3.0}})
        )

    def test_error_and_empty_face_count_fail(self) -> None:
        self.assertFalse(reference_detection_ok(None))
        self.assertFalse(reference_detection_ok({"head_pose": {"error": "未检测到人脸"}}))
        self.assertFalse(
            reference_detection_ok({"face_detect": {"face_count": 0, "main_face": None}})
        )

    def test_detected_face_counts_as_success(self) -> None:
        self.assertTrue(
            reference_detection_ok(
                {"face_detect": {"face_count": 1, "main_face": {"score": 0.9}}}
            )
        )


class TestExampleOrientationAndSize(unittest.TestCase):
    def test_exif_applied_before_optional_resize(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "side.jpg")
            _save_marked_jpeg(path, 8)
            full = load_example_image_pil(path, 0)
            small = load_example_image_pil(path, 8)
            halved = load_example_image_pil(path, 0, half=True)
            with Image.open(path) as raw:
                expected = ImageOps.exif_transpose(raw).convert("RGB")
        self.assertIsNotNone(full)
        assert full is not None and small is not None and halved is not None
        self.assertEqual(full.size, (10, 30))
        self.assertEqual(full.getpixel((0, 0)), expected.getpixel((0, 0)))
        self.assertEqual(max(small.size), 8)
        self.assertLess(small.size[0], full.size[0])
        self.assertEqual(halved.size, (full.size[0] // 2, full.size[1] // 2))


class TestReferenceFallback(unittest.TestCase):
    def test_uses_later_candidate_when_first_detection_fails(self) -> None:
        def fake(_path: str, _details: dict):
            if _path.endswith("a.jpg"):
                return {"head_pose": {"error": "未检测到人脸"}}
            return {"head_pose": {"yaw": 1.0, "pitch": 2.0, "roll": 3.0}}

        with patch.object(VLMClient, "_example_tool_results", staticmethod(fake)):
            chosen = VLMClient._choose_reference_candidate(
                "head_pose",
                "frontal",
                ["/tmp/a.jpg", "/tmp/b.jpg"],
                {},
                True,
            )
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen[0], "/tmp/b.jpg")
        self.assertEqual(chosen[1]["head_pose"]["yaw"], 1.0)

    def test_all_failed_keeps_the_first_image_and_its_result(self) -> None:
        failed = {"face_detect": {"face_count": 0, "main_face": None}}

        def fake(_path: str, _details: dict):
            return failed

        with patch.object(VLMClient, "_example_tool_results", staticmethod(fake)):
            chosen = VLMClient._choose_reference_candidate(
                "face_size",
                "0",
                ["/tmp/a.jpg", "/tmp/b.jpg"],
                {},
                True,
            )
        self.assertEqual(chosen, ("/tmp/a.jpg", failed))

    def test_without_tools_does_not_walk_candidates(self) -> None:
        with patch.object(VLMClient, "_example_tool_results", side_effect=AssertionError("no tools")):
            chosen = VLMClient._choose_reference_candidate(
                "gender",
                "male",
                ["/tmp/a.jpg", "/tmp/b.jpg"],
                {},
                False,
            )
        self.assertEqual(chosen, ("/tmp/a.jpg", None))


def _image_count(content: list) -> int:
    return sum(1 for part in content if part.get("type") == "image_url")


class TestReferencePayload(unittest.TestCase):
    def test_unbound_question_keeps_the_original_photo(self) -> None:
        b64, tools = reference_payload_for_vlm([], "orig", None)
        self.assertEqual(b64, "orig")
        self.assertIsNone(tools)

    def test_bound_tool_drops_the_original_and_any_crop(self) -> None:
        b64, tools = reference_payload_for_vlm(
            ["face_detect"],
            "orig",
            {"face_detect": {"face_count": 1, "height_ratio": 0.4, "_images": ["crop"]}},
        )
        self.assertEqual(b64, "")
        assert tools is not None
        self.assertNotIn("_images", tools["face_detect"])
        self.assertEqual(tools["face_detect"]["height_ratio"], 0.4)

    def test_face_crop_keeps_only_the_cropped_photo(self) -> None:
        b64, tools = reference_payload_for_vlm(
            ["face_crop"],
            "orig",
            {"face_crop": {"face_count": 1, "_images": ["crop-a"]}},
        )
        self.assertEqual(b64, "")
        assert tools is not None
        self.assertEqual(tools["face_crop"]["_images"], ["crop-a"])

    def test_message_uploads_crop_instead_of_reference_original(self) -> None:
        client = VLMClient.__new__(VLMClient)
        target = Image.new("RGB", (8, 8), (1, 2, 3))
        messages = client._messages_with_image(
            target,
            "prompt",
            examples=[
                (
                    "eyewear",
                    "1",
                    "",
                    {"face_attribute": {"glasses": "yes"}},
                ),
                (
                    "makeup",
                    "1",
                    "",
                    {"face_crop": {"face_count": 1, "_images": ["crop-b64"]}},
                ),
                ("hairstyle", "long", "original-b64", None),
            ],
        )
        content = messages[0]["content"]
        urls = [
            part["image_url"]["url"]
            for part in content
            if part.get("type") == "image_url"
        ]
        self.assertEqual(_image_count(content), 3)
        self.assertIn("crop-b64", urls[0])
        self.assertIn("original-b64", urls[1])
        self.assertNotIn("orig-should-be-absent", "".join(urls))
        text = "\n".join(
            part.get("text", "") for part in content if part.get("type") == "text"
        )
        self.assertIn("[Reference measurement: face_attribute]", text)
        self.assertIn("[Reference crop: face_crop face #1]", text)


if __name__ == "__main__":
    unittest.main()
