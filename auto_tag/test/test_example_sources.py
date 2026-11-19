"""参考样图：一个档位一条路径；文件夹最多 1 张。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

from auto_tag.core.example_sources import (
    example_source_path,
    planned_example_rows,
    resolve_example_path,
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
        self.assertEqual([os.path.basename(p) for p in rows[0][3]], ["a.jpg"])
        self.assertEqual([os.path.basename(p) for p in rows[1][3]], ["one.png"])


class TestResolveExamplePath(unittest.TestCase):
    def test_windows_drive_converts_before_config_join_on_linux(self) -> None:
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch("os.path.realpath", side_effect=lambda p: p), \
                mock.patch("os.path.abspath", side_effect=lambda p: p):
            back = resolve_example_path(r"D:\dev\sample_image\indoor")
            forward = resolve_example_path("D:/dev/sample_image/indoor")
        self.assertEqual(back, "/mnt/d/dev/sample_image/indoor")
        self.assertEqual(forward, "/mnt/d/dev/sample_image/indoor")

    def test_windows_drive_directory_is_listed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "a.jpg"))
            with mock.patch(
                "auto_tag.core.example_sources.windows_drive_path_to_posix_mount",
                return_value=root,
            ):
                chosen, truncated = take_example_images(
                    [r"D:\dev\sample_image\indoor"]
                )
        self.assertEqual([os.path.basename(p) for p in chosen], ["a.jpg"])
        self.assertFalse(truncated)

    def test_relative_path_still_joins_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            image = os.path.join(root, "only.jpg")
            _touch(image)
            with mock.patch(
                "auto_tag.core.config.config_json_path",
                os.path.join(root, "config.json"),
            ):
                got = resolve_example_path("only.jpg")
        self.assertEqual(os.path.realpath(got), os.path.realpath(image))


class TestTakeExampleImages(unittest.TestCase):
    def test_directory_uses_first_image_in_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "c.jpg"))
            _touch(os.path.join(root, "a.PNG"))
            _touch(os.path.join(root, "b.jpeg"))
            _touch(os.path.join(root, "notes.txt"))
            _touch(os.path.join(root, "sub", "z.jpg"))
            chosen, truncated = take_example_images([root])
        names = [os.path.basename(p) for p in chosen]
        self.assertEqual(names, ["a.PNG"])
        self.assertTrue(truncated)

    def test_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            image = os.path.join(root, "only.jpg")
            _touch(image)
            chosen, truncated = take_example_images([image])
        self.assertEqual([os.path.basename(p) for p in chosen], ["only.jpg"])
        self.assertFalse(truncated)

    def test_current_directory_wins_over_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "only.jpg"))
            _touch(os.path.join(root, "nested", "n.png"))
            chosen, truncated = take_example_images([root])
        self.assertEqual(
            [os.path.relpath(p, root).replace("\\", "/") for p in chosen],
            ["only.jpg"],
        )
        self.assertTrue(truncated)

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
