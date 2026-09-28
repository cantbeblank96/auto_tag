"""系统目录选择窗口：命令构造与取消/失败分支。不弹出真实对话框。"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException

from auto_tag.backend.routers.health import PickDirectoryBody, pick_directory
from auto_tag.core.utils import directory_dialog as dd
from auto_tag.core.utils.directory_dialog import (
    DirectoryDialogBusy,
    DirectoryDialogError,
    DirectoryPickResult,
    _resolve_wsl_powershell,
    build_kdialog_argv,
    build_osascript_argv,
    build_windows_picker_argv,
    build_zenity_argv,
    pick_existing_directory,
)


def _proc(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["dialog"], returncode=code, stdout=stdout, stderr=stderr)


class TestWindowsPickerCommand(unittest.TestCase):
    def test_encoded_script_uses_any_drive_dialog(self) -> None:
        argv = build_windows_picker_argv()
        self.assertEqual(argv[0], "powershell.exe")
        self.assertIn("-STA", argv)
        self.assertIn("-EncodedCommand", argv)
        encoded = argv[-1]
        script = base64.b64decode(encoded).decode("utf-16le")
        self.assertIn("FolderBrowserDialog", script)
        self.assertIn("MyComputer", script)
        self.assertIn("ShowNewFolderButton = $false", script)
        self.assertIn("AUTO_TAG_PICK_TITLE", script)
        self.assertIn("AUTO_TAG_PICK_INITIAL", script)
        self.assertIn("exit 0", script)
        self.assertIn("exit 2", script)

    def test_title_is_not_embedded_in_the_command(self) -> None:
        argv = build_windows_picker_argv()
        joined = " ".join(argv)
        self.assertNotIn("选择输入目录", joined)
        self.assertNotIn("D:\\", joined)


class TestPosixPickerCommands(unittest.TestCase):
    def test_zenity_directory_and_initial(self) -> None:
        argv = build_zenity_argv("选择输入目录", "/data/images")
        self.assertEqual(argv[:3], ["zenity", "--file-selection", "--directory"])
        self.assertIn("--filename", argv)
        self.assertTrue(argv[-1].endswith("/"))

    def test_kdialog_and_osascript(self) -> None:
        self.assertIn("--getexistingdirectory", build_kdialog_argv("t", "/tmp"))
        argv = build_osascript_argv('选择 "目录"')
        self.assertEqual(argv[0], "osascript")
        self.assertIn("choose folder", argv[-1])
        self.assertIn("选择 '目录'", argv[-1])


class TestPickExistingDirectory(unittest.TestCase):
    def setUp(self) -> None:
        # 测试机若本身在 WSL 中，不能让宿主环境把 Linux 用例拐到 Windows 对话框。
        self._wsl = mock.patch.object(dd, "_is_wsl", return_value=False)
        self._wsl.start()

    def tearDown(self) -> None:
        self._wsl.stop()

    def test_linux_without_display(self) -> None:
        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(DirectoryDialogError) as ctx:
                    pick_existing_directory()
        self.assertIn("图形界面", str(ctx.exception))

    def test_zenity_success_returns_realpath(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            real = os.path.realpath(raw)
            with mock.patch.object(sys, "platform", "linux"):
                with mock.patch.dict(os.environ, {"DISPLAY": ":1"}):
                    with mock.patch("auto_tag.core.utils.directory_dialog.shutil.which", return_value="/usr/bin/zenity"):
                        with mock.patch.object(dd, "_run_command", return_value=_proc(0, real + "\n")) as run:
                            result = pick_existing_directory("选择输入目录", initial_dir=real)
            run.assert_called_once()
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], "zenity")
            self.assertIn(real + "/", argv)
            self.assertFalse(result.cancelled)
            self.assertEqual(result.path, real)

    def test_zenity_cancel(self) -> None:
        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
                with mock.patch("auto_tag.core.utils.directory_dialog.shutil.which", return_value="/usr/bin/zenity"):
                    with mock.patch.object(dd, "_run_command", return_value=_proc(1)):
                        result = pick_existing_directory()
        self.assertTrue(result.cancelled)
        self.assertIsNone(result.path)

    def test_zenity_display_error(self) -> None:
        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch.dict(os.environ, {"DISPLAY": ":1"}):
                with mock.patch("auto_tag.core.utils.directory_dialog.shutil.which", return_value="/usr/bin/zenity"):
                    with mock.patch.object(
                        dd,
                        "_run_command",
                        return_value=_proc(1, stderr="cannot open display: :1"),
                    ):
                        with self.assertRaises(DirectoryDialogError):
                            pick_existing_directory()

    def test_missing_directory_is_rejected(self) -> None:
        missing = "/tmp/auto_tag_pick_missing_dir_should_not_exist"
        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch.dict(os.environ, {"DISPLAY": ":1"}):
                with mock.patch("auto_tag.core.utils.directory_dialog.shutil.which", return_value="/usr/bin/zenity"):
                    with mock.patch.object(dd, "_run_command", return_value=_proc(0, missing)):
                        with self.assertRaises(DirectoryDialogError):
                            pick_existing_directory()

    def test_falls_back_to_kdialog_then_tk(self) -> None:
        def which(name: str) -> str | None:
            if name == "kdialog":
                return "/usr/bin/kdialog"
            return None

        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch.dict(os.environ, {"DISPLAY": ":1"}):
                with mock.patch("auto_tag.core.utils.directory_dialog.shutil.which", side_effect=which):
                    with mock.patch.object(dd, "_run_command", return_value=_proc(2)) as run:
                        result = pick_existing_directory()
        self.assertEqual(run.call_args.args[0][0], "kdialog")
        self.assertTrue(result.cancelled)

        def which_none(_name: str) -> None:
            return None

        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch.dict(os.environ, {"DISPLAY": ":1"}):
                with mock.patch("auto_tag.core.utils.directory_dialog.shutil.which", side_effect=which_none):
                    with mock.patch.object(dd, "_tk_importable", return_value=True):
                        with mock.patch.object(dd, "_run_command", return_value=_proc(2)) as run:
                            result = pick_existing_directory()
        self.assertEqual(run.call_args.args[0][2], "auto_tag.core.utils.directory_dialog")
        self.assertIn("--backend", run.call_args.args[0])
        self.assertTrue(result.cancelled)

    def test_windows_cancel_and_drive_path_passthrough(self) -> None:
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(dd, "_resolve_powershell", return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"):
                with mock.patch.object(dd, "_run_command", return_value=_proc(2)) as run:
                    result = pick_existing_directory("选择输入目录", initial_dir=r"D:\images")
        argv = run.call_args.args[0]
        self.assertTrue(argv[0].lower().endswith("powershell.exe"))
        self.assertIn("-STA", argv)
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["AUTO_TAG_PICK_TITLE"], "选择输入目录")
        # D:\images 在 Linux 上不是目录，初始目录应留空，避免对话框定位失败
        self.assertEqual(env["AUTO_TAG_PICK_INITIAL"], "")
        self.assertTrue(result.cancelled)

    def test_windows_powershell_failure_falls_back_to_tk(self) -> None:
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(dd, "_resolve_powershell", side_effect=DirectoryDialogError("no ps")):
                with mock.patch.object(dd, "_tk_importable", return_value=True):
                    with mock.patch.object(dd, "_run_command", return_value=_proc(2)) as run:
                        result = pick_existing_directory()
        self.assertIn("--backend", run.call_args.args[0])
        self.assertTrue(result.cancelled)

    def test_wsl_uses_windows_dialog_not_tk(self) -> None:
        with mock.patch.object(dd, "_is_wsl", return_value=True):
            with mock.patch.object(sys, "platform", "linux"):
                with mock.patch.object(dd, "_usable_initial", return_value="/mnt/d/data/images"):
                    with mock.patch.object(dd, "_resolve_powershell", return_value="powershell.exe"):
                        with mock.patch.object(dd, "_run_command", return_value=_proc(0, r"D:\data\images")) as run:
                            with mock.patch.object(dd, "normalize_fs_path", return_value="/mnt/d/data/images"):
                                with mock.patch("os.path.isdir", return_value=True):
                                    result = pick_existing_directory(
                                        "选择输入目录",
                                        initial_dir="/mnt/d/data/images",
                                    )
        argv = run.call_args.args[0]
        self.assertTrue(argv[0].lower().endswith("powershell.exe"))
        self.assertNotIn("directory_dialog", argv)
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["AUTO_TAG_PICK_INITIAL"], r"D:\data\images")
        self.assertFalse(result.cancelled)
        self.assertEqual(result.path, r"D:\data\images")

    def test_wsl_resolver_uses_windows_powershell_exe(self) -> None:
        with mock.patch("auto_tag.core.utils.directory_dialog.shutil.which", return_value=None):
            with mock.patch("os.path.isfile", return_value=True):
                found = _resolve_wsl_powershell()
        self.assertEqual(
            found,
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        )

        def which(name: str) -> str | None:
            if name == "pwsh":
                return "/usr/bin/pwsh"
            if name == "powershell.exe":
                return "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            return None

        with mock.patch("auto_tag.core.utils.directory_dialog.shutil.which", side_effect=which):
            found = _resolve_wsl_powershell()
        self.assertTrue(found.endswith("powershell.exe"))
        self.assertNotIn("pwsh", found)

    def test_wsl_does_not_fall_back_to_tk(self) -> None:
        with mock.patch.object(dd, "_is_wsl", return_value=True):
            with mock.patch.object(sys, "platform", "linux"):
                with mock.patch.object(dd, "_resolve_powershell", return_value="powershell.exe"):
                    with mock.patch.object(dd, "_tk_importable", return_value=True):
                        with mock.patch.object(dd, "_run_command", return_value=_proc(1, stderr="boom")) as run:
                            with self.assertRaises(DirectoryDialogError):
                                pick_existing_directory()
        self.assertNotIn("directory_dialog", run.call_args.args[0])

    def test_busy_when_dialog_already_open(self) -> None:
        self.assertTrue(dd._DIALOG_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(DirectoryDialogBusy):
                pick_existing_directory()
        finally:
            dd._DIALOG_LOCK.release()


class TestPickDirectoryRoute(unittest.TestCase):
    def test_cancel_payload(self) -> None:
        with mock.patch(
            "auto_tag.backend.routers.health.pick_existing_directory",
            return_value=DirectoryPickResult(cancelled=True, path=None),
        ):
            body = pick_directory(PickDirectoryBody())
        self.assertEqual(body, {"cancelled": True, "path": None})

    def test_busy_is_409(self) -> None:
        with mock.patch(
            "auto_tag.backend.routers.health.pick_existing_directory",
            side_effect=DirectoryDialogBusy("已有目录选择窗口打开"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                pick_directory(PickDirectoryBody())
        self.assertEqual(ctx.exception.status_code, 409)

    def test_dialog_error_is_503(self) -> None:
        with mock.patch(
            "auto_tag.backend.routers.health.pick_existing_directory",
            side_effect=DirectoryDialogError("没有图形界面"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                pick_directory(PickDirectoryBody(title="选择输入目录"))
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("图形界面", str(ctx.exception.detail))
