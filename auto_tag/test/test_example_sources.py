"""参考样图：一个档位一条路径；文件夹最多 2 张。"""
from __future__ import annotations

import os
import tempfile
import unittest

from auto_tag.core.example_sources import (
    EXAMPLE_IMAGES_PER_VALUE,
    example_source_path,
    planned_example_rows,
    take_example_images,
)


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x")


class TestExampleSourcePath(unittest.TestCase):
    def test_string_and_last_list_item_wins(self) -> None:
        self.assertEqual(example_source_path("  a.jpg "), "a.jpg")
        self.assertEqual(example_source_path([" a.jpg ", "", "b.png"]), "b.png")
        self.assertEqual(example_source_path(None), "")
        self.assertEqual(example_source_path({"a": 1}), "")


class TestPlannedExampleRows(unittest.TestCase):
    def test_records_value_and_resolved_paths(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "b.jpg"))
            _touch(os.path.join(root, "a.jpg"))
            _touch(os.path.join(root, "c.jpg"))
            single = os.path.join(root, "one.png")
            _touch(single)
            rows = planned_example_rows({
                "brightness": {
                    "examples": {
                        "2.5": root,
                        "7.5": single,
                    }
                }
            })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "brightness")
        self.assertEqual(rows[0][1], "2.5")
        self.assertEqual([os.path.basename(p) for p in rows[0][3]], ["a.jpg", "b.jpg"])
        self.assertEqual([os.path.basename(p) for p in rows[1][3]], ["one.png"])


class TestTakeExampleImages(unittest.TestCase):
    def test_directory_uses_first_two_in_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "c.jpg"))
            _touch(os.path.join(root, "a.PNG"))
            _touch(os.path.join(root, "b.jpeg"))
            _touch(os.path.join(root, "notes.txt"))
            _touch(os.path.join(root, "sub", "z.jpg"))
            chosen, truncated = take_example_images([root])
        names = [os.path.basename(p) for p in chosen]
        self.assertEqual(names, ["a.PNG", "b.jpeg"])
        self.assertTrue(truncated)
        self.assertEqual(EXAMPLE_IMAGES_PER_VALUE, 2)

    def test_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            image = os.path.join(root, "only.jpg")
            _touch(image)
            chosen, truncated = take_example_images([image])
        self.assertEqual([os.path.basename(p) for p in chosen], ["only.jpg"])
        self.assertFalse(truncated)

    def test_subdirectory_fills_remaining_slot(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "only.jpg"))
            _touch(os.path.join(root, "nested", "n.png"))
            chosen, truncated = take_example_images([root])
        self.assertEqual(
            [os.path.relpath(p, root).replace("\\", "/") for p in chosen],
            ["only.jpg", "nested/n.png"],
        )
        self.assertFalse(truncated)

    def test_unreadable_file_in_folder_does_not_consume_a_slot(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "bad.jpg"))
            _touch(os.path.join(root, "good.jpg"))
            chosen, truncated = take_example_images(
                [root],
                accept=lambda path: os.path.basename(path) != "bad.jpg",
            )
        self.assertEqual([os.path.basename(p) for p in chosen], ["good.jpg"])
        self.assertFalse(truncated)

    def test_missing_and_non_image_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            text = os.path.join(root, "readme.txt")
            _touch(os.path.join(root, "ok.jpg"))
            with open(text, "wb") as fh:
                fh.write(b"t")
            missing = os.path.join(root, "nope.jpg")
            chosen, truncated = take_example_images([root])
            self.assertEqual([os.path.basename(p) for p in chosen], ["ok.jpg"])
            self.assertFalse(truncated)
            chosen, truncated = take_example_images([missing])
            self.assertEqual(chosen, [])
            self.assertFalse(truncated)
            chosen, truncated = take_example_images([text])
            self.assertEqual(chosen, [])
            self.assertFalse(truncated)
