"""Windows / WSL 路径互转：单元测试。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from auto_tag.core.utils.path_utils import (
    normalize_fs_path,
    windows_drive_path_to_posix_mount,
    posix_mount_to_windows_drive_path,
)


class TestWindowsDriveToPosix(unittest.TestCase):
    def test_backslash(self) -> None:
        self.assertEqual(
            windows_drive_path_to_posix_mount(r"D:\dev\little_data\hunhe"),
            "/mnt/d/dev/little_data/hunhe",
        )

    def test_forward_slash(self) -> None:
        self.assertEqual(
            windows_drive_path_to_posix_mount("D:/dev/little_data/hunhe"),
            "/mnt/d/dev/little_data/hunhe",
        )

    def test_mixed_separators(self) -> None:
        self.assertEqual(
            windows_drive_path_to_posix_mount(r"D:\dev/little_data/hunhe/sub"),
            "/mnt/d/dev/little_data/hunhe/sub",
        )

    def test_drive_root(self) -> None:
        self.assertEqual(windows_drive_path_to_posix_mount(r"D:\\"), "/mnt/d")
        self.assertEqual(windows_drive_path_to_posix_mount("E:/"), "/mnt/e")

    def test_non_windows_unchanged(self) -> None:
        self.assertIsNone(windows_drive_path_to_posix_mount("/mnt/d/dev/foo"))
        self.assertIsNone(windows_drive_path_to_posix_mount("relative/path"))


class TestPosixMountToWindows(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(
            posix_mount_to_windows_drive_path("/mnt/d/dev/little_data/hunhe"),
            r"D:\dev\little_data\hunhe",
        )

    def test_drive_root(self) -> None:
        self.assertEqual(posix_mount_to_windows_drive_path("/mnt/d"), "D:\\")
        self.assertEqual(posix_mount_to_windows_drive_path("/mnt/d/"), "D:\\")

    def test_non_mount_unchanged(self) -> None:
        self.assertIsNone(posix_mount_to_windows_drive_path(r"D:\dev\foo"))
        self.assertIsNone(posix_mount_to_windows_drive_path("/home/user"))


class TestNormalizeFsPathPlatform(unittest.TestCase):
    def test_linux_converts_windows_drive(self) -> None:
        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch("os.path.realpath", side_effect=lambda p: p):
                with mock.patch("os.path.abspath", side_effect=lambda p: p):
                    with mock.patch("os.path.expanduser", side_effect=lambda p: p):
                        got = normalize_fs_path(r"D:\dev\little_data\hunhe")
        self.assertEqual(got, "/mnt/d/dev/little_data/hunhe")

    def test_win32_converts_mnt(self) -> None:
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch("os.path.realpath", side_effect=lambda p: p):
                with mock.patch("os.path.abspath", side_effect=lambda p: p):
                    with mock.patch("os.path.expanduser", side_effect=lambda p: p):
                        got = normalize_fs_path("/mnt/d/dev/little_data/hunhe")
        self.assertEqual(got, r"D:\dev\little_data\hunhe")

    def test_empty(self) -> None:
        self.assertEqual(normalize_fs_path("  "), "")
        self.assertEqual(normalize_fs_path(""), "")


if __name__ == "__main__":
    # 在仓库根目录：PYTHONPATH=. python -m auto_tag.test.test_normalize_fs_path
    unittest.main()
