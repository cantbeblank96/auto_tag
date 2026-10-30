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
    def test_windows_command_uses_sta_and_fits_command_line_limit(self) -> None:
        argv = build_windows_picker_argv()
        self.assertEqual(argv[0], "powershell.exe")
        self.assertIn("-STA", argv)
        self.assertIn("-EncodedCommand", argv)
        self.assertLess(len(subprocess.list2cmdline(argv)), 32767)

    def test_picker_carries_native_source_and_unicode_inputs_in_environment(self) -> None:
        with mock.patch.object(dd, "_is_wsl", return_value=False):
            env = dd._picker_env("选择训练图片", r"D:\含空格的目录\训练集")
        self.assertEqual(env["AUTO_TAG_PICK_TITLE"], "选择训练图片")
        self.assertEqual(env["AUTO_TAG_PICK_INITIAL"], r"D:\含空格的目录\训练集")
        self.assertTrue(env["AUTO_TAG_PICK_SOURCE"])
        # 源文件走环境而非 EncodedCommand，给真实 Windows 路径和参数留足空间。
        self.assertLess(len(env["AUTO_TAG_PICK_SOURCE"]), 32767)

    def test_title_is_not_embedded_in_the_command(self) -> None:
        argv = build_windows_picker_argv()
        joined = " ".join(argv)
        self.assertNotIn("选择输入目录", joined)
        self.assertNotIn("D:\\", joined)

    def test_wsl_forwards_picker_variables_without_path_conversion(self) -> None:
        original = "KEEP_ME/p:AUTO_TAG_PICK_SOURCE/lp:AUTO_TAG_PICK_TITLE/u"
        with mock.patch.object(dd, "_is_wsl", return_value=True), \
             mock.patch.dict(os.environ, {"WSLENV": original}):
            env = dd._picker_env("选择目录", "/mnt/d/训练图")
            self.assertEqual(os.environ["WSLENV"], original)
        self.assertEqual(env["AUTO_TAG_PICK_INITIAL"], "D:\\训练图")
        self.assertEqual(set(env["WSLENV"].split(":")), {
            "KEEP_ME/p", "AUTO_TAG_PICK_TITLE/w", "AUTO_TAG_PICK_INITIAL/w", "AUTO_TAG_PICK_SOURCE/w",
        })

    def test_missing_native_component_reports_actionable_error(self) -> None:
        with mock.patch("builtins.open", side_effect=OSError("missing source")), \
             mock.patch.object(dd, "_is_wsl", return_value=False):
            with self.assertRaisesRegex(DirectoryDialogError, "Windows 目录选择组件"):
                dd._picker_env("选择目录", None)

    @unittest.skipUnless(sys.platform == "win32" or dd._is_wsl(), "需要 Windows PowerShell 和原生 COM")
    def test_native_com_options_work_without_showing_dialog(self) -> None:
        # 编译真实 C# 源并调用真实 COM 方法；不弹窗、不修改 auto_tag 的历史 GUID。
        script = r'''
$ErrorActionPreference = "Stop"
try {
    Add-Type -TypeDefinition $env:AUTO_TAG_PICK_SOURCE -Language CSharp
    $assembly = [AutoTag.WindowsFolderPicker].Assembly
    $dialogType = $assembly.GetType("AutoTag.FileOpenDialog")
    $interfaceType = $assembly.GetType("AutoTag.IFileOpenDialog")
    $dialog = [Activator]::CreateInstance($dialogType)
    try {
        $guidArgs = [object[]]@([Guid]::NewGuid())
        $hr = $interfaceType.GetMethod("SetClientGuid").Invoke($dialog, $guidArgs)
        [Runtime.InteropServices.Marshal]::ThrowExceptionForHR([int]$hr)
        $getArgs = [object[]]@([uint32]0)
        $hr = $interfaceType.GetMethod("GetOptions").Invoke($dialog, $getArgs)
        [Runtime.InteropServices.Marshal]::ThrowExceptionForHR([int]$hr)
        $options = [uint32]$getArgs[0] -bor 0x20 -bor 0x40 -bor 0x800
        $setArgs = [object[]]@([uint32]$options)
        $hr = $interfaceType.GetMethod("SetOptions").Invoke($dialog, $setArgs)
        [Runtime.InteropServices.Marshal]::ThrowExceptionForHR([int]$hr)
        $hr = $interfaceType.GetMethod("GetOptions").Invoke($dialog, $getArgs)
        [Runtime.InteropServices.Marshal]::ThrowExceptionForHR([int]$hr)
        if (($getArgs[0] -band 0x860) -ne 0x860) { throw "Folder options were not applied" }
        [Console]::Out.WriteLine("COM_OK")
    } finally {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($dialog)
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
'''
        argv = build_windows_picker_argv()
        argv[0] = dd._resolve_powershell()
        argv[-1] = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        proc = subprocess.run(argv, env=dd._picker_env("测试", None), capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("COM_OK", proc.stdout)


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

    def test_windows_powershell_failure_is_reported_without_legacy_fallback(self) -> None:
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(dd, "_resolve_powershell", side_effect=DirectoryDialogError("no ps")):
                with mock.patch.object(dd, "_tk_importable", return_value=True):
                    with mock.patch.object(dd, "_run_command", return_value=_proc(2)) as run:
                        with self.assertRaises(DirectoryDialogError):
                            pick_existing_directory()
        run.assert_not_called()

    def test_windows_unexpected_exit_is_an_error_and_releases_dialog_lock(self) -> None:
        for proc in (_proc(1), _proc(1, stderr="COM failed"), _proc(2, stderr="bad command"), _proc(0)):
            with self.subTest(returncode=proc.returncode, stderr=proc.stderr):
                with mock.patch.object(sys, "platform", "win32"), \
                     mock.patch.object(dd, "_resolve_powershell", return_value="powershell.exe"), \
                     mock.patch.object(dd, "_tk_importable", return_value=True), \
                     mock.patch.object(dd, "_run_command", return_value=proc), \
                     mock.patch.object(dd, "_pick_tk", return_value=None) as fallback:
                    with self.assertRaises(DirectoryDialogError):
                        pick_existing_directory()
                fallback.assert_not_called()
                self.assertTrue(dd._DIALOG_LOCK.acquire(blocking=False))
                dd._DIALOG_LOCK.release()

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
