"""在运行后端的电脑上打开系统目录选择窗口。

浏览器的目录选择控件不会把绝对路径交给网页，因此由本机进程弹出系统对话框，
再把选中的绝对路径返回给前端填入「输入目录」。

- Windows 原生，以及 WSL 中的后端：powershell.exe + FolderBrowserDialog
  （系统「浏览文件夹」，根节点为「此电脑」，可选任意盘符）
- macOS：osascript choose folder，失败时回退 tkinter
- Linux 本机：zenity，其次 kdialog，再回退 tkinter

WSL 里 sys.platform 仍是 linux。若按 Linux 回退到 tkinter，WSLg 会在 Windows
桌面上画出 Linux 风格的目录窗口。因此 WSL 只打开 Windows 对话框。
"""
from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Optional

from auto_tag.core.utils.path_utils import normalize_fs_path, posix_mount_to_windows_drive_path

_DIALOG_LOCK = threading.Lock()

# 由环境变量传入标题和初始目录，避免把用户路径拼进命令行。
_WINDOWS_PICKER_SCRIPT = r"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$title = [string]$env:AUTO_TAG_PICK_TITLE
if (-not $title) { $title = "选择目录" }
$dlg.Description = $title
$dlg.ShowNewFolderButton = $false
# MyComputer = 此电脑，展开后可选任意本地盘符与已映射磁盘
$dlg.RootFolder = [System.Environment+SpecialFolder]::MyComputer
$initial = [string]$env:AUTO_TAG_PICK_INITIAL
if ($initial -and (Test-Path -LiteralPath $initial -PathType Container)) {
    $dlg.SelectedPath = $initial
}
# 用置顶的空窗体当 owner，避免对话框被浏览器挡在后面；不要最小化，否则对话框可能一起看不见
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$owner.StartPosition = "CenterScreen"
$owner.Width = 1
$owner.Height = 1
$owner.Show()
$owner.Activate()
try {
    $result = $dlg.ShowDialog($owner)
} finally {
    $owner.Close()
    $owner.Dispose()
}
if ($result -eq [System.Windows.Forms.DialogResult]::OK -and $dlg.SelectedPath) {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = $utf8
    [Console]::Out.WriteLine($dlg.SelectedPath)
    exit 0
}
exit 2
"""


class DirectoryDialogError(Exception):
    """当前环境无法打开系统目录窗口。"""


class DirectoryDialogBusy(Exception):
    """已经有一个目录窗口开着。"""


@dataclass
class DirectoryPickResult:
    cancelled: bool
    path: Optional[str]


def build_windows_picker_argv() -> list[str]:
    """构造 Windows 目录窗口命令。标题与初始目录走环境变量，不进命令行。"""
    encoded = base64.b64encode(_WINDOWS_PICKER_SCRIPT.encode("utf-16le")).decode("ascii")
    return [
        "powershell.exe",
        "-STA",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def build_zenity_argv(title: str, initial: Optional[str]) -> list[str]:
    cmd = [
        "zenity",
        "--file-selection",
        "--directory",
        "--title",
        title or "选择目录",
    ]
    if initial:
        filename = initial if initial.endswith("/") else initial + "/"
        cmd.extend(["--filename", filename])
    return cmd


def build_kdialog_argv(title: str, initial: Optional[str]) -> list[str]:
    start = initial or os.path.expanduser("~")
    return [
        "kdialog",
        "--title",
        title or "选择目录",
        "--getexistingdirectory",
        start,
    ]


def build_osascript_argv(title: str) -> list[str]:
    prompt = (title or "选择目录").replace("\\", "").replace('"', "'")
    script = f'POSIX path of (choose folder with prompt "{prompt}")'
    return ["osascript", "-e", script]


def build_tk_argv(title: str, initial: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "auto_tag.core.utils.directory_dialog",
        "--backend",
        "tk",
        "--title",
        title or "选择目录",
        "--initial",
        initial or "",
    ]


def pick_existing_directory(
    title: str = "选择目录",
    initial_dir: Optional[str] = None,
) -> DirectoryPickResult:
    """弹出系统目录窗口。取消时 cancelled=True；失败抛 DirectoryDialogError。"""
    if not _DIALOG_LOCK.acquire(blocking=False):
        raise DirectoryDialogBusy("已有目录选择窗口打开，请先在该窗口中完成选择或取消")
    try:
        raw = _pick_raw(title or "选择目录", _usable_initial(initial_dir))
        if raw is None:
            return DirectoryPickResult(cancelled=True, path=None)
        path = normalize_fs_path(raw)
        if not path or not os.path.isdir(path):
            raise DirectoryDialogError(f"所选路径不是可用目录：{raw}")
        # 对话框是 Windows 的，填回页面也用盘符路径（D:\...），与窗口里看到的一致。
        return DirectoryPickResult(cancelled=False, path=_path_for_client(path))
    finally:
        _DIALOG_LOCK.release()


def _usable_initial(initial_dir: Optional[str]) -> Optional[str]:
    raw = (initial_dir or "").strip()
    if not raw:
        return None
    try:
        path = normalize_fs_path(raw)
    except OSError:
        return None
    if path and os.path.isdir(path):
        return path
    return None


def _is_wsl() -> bool:
    """当前进程是否跑在 WSL 中。这种环境下目录窗口应使用 Windows 原生对话框。"""
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read().lower()
    except OSError:
        return False
    return "microsoft" in text


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _pick_raw(title: str, initial: Optional[str]) -> Optional[str]:
    if sys.platform == "win32" or _is_wsl():
        return _pick_windows(title, initial)
    if sys.platform == "darwin":
        return _pick_darwin(title, initial)
    return _pick_linux(title, initial)


def _pick_windows(title: str, initial: Optional[str]) -> Optional[str]:
    env = _picker_env(title, initial)
    try:
        argv = build_windows_picker_argv()
        argv[0] = _resolve_powershell()
        proc = _run_command(argv, env=env)
    except DirectoryDialogError:
        if _is_wsl() or not _tk_importable():
            raise
        return _pick_tk(title, initial)
    outcome = _interpret(proc, kind="windows")
    if outcome.failed:
        if _is_wsl() or not _tk_importable():
            raise DirectoryDialogError(outcome.path or "无法打开 Windows 目录窗口")
        return _pick_tk(title, initial)
    return outcome.path


def _pick_darwin(title: str, initial: Optional[str]) -> Optional[str]:
    if shutil.which("osascript"):
        proc = _run_command(build_osascript_argv(title))
        outcome = _interpret(proc, kind="osascript")
        if not outcome.failed:
            return outcome.path
    return _pick_tk(title, initial)


def _pick_linux(title: str, initial: Optional[str]) -> Optional[str]:
    if not _has_display():
        raise DirectoryDialogError(
            "当前环境没有图形界面，无法打开目录窗口。请直接在文本框填写绝对路径。"
        )
    if shutil.which("zenity"):
        proc = _run_command(build_zenity_argv(title, initial))
        return _interpret(proc, kind="zenity").unwrap()
    if shutil.which("kdialog"):
        proc = _run_command(build_kdialog_argv(title, initial))
        return _interpret(proc, kind="kdialog").unwrap()
    return _pick_tk(title, initial)


def _pick_tk(title: str, initial: Optional[str]) -> Optional[str]:
    if _is_wsl():
        raise DirectoryDialogError(
            "当前是 WSL 后端，应打开 Windows 目录窗口。未找到 powershell.exe 时"
            "请直接填写路径，例如 D:\\images。"
        )
    if sys.platform != "win32" and not _has_display():
        raise DirectoryDialogError(
            "当前环境没有图形界面，无法打开目录窗口。请直接在文本框填写绝对路径。"
        )
    if not _tk_importable():
        raise DirectoryDialogError(
            "无法打开目录选择窗口：未找到系统目录工具（Windows 需要 powershell，"
            "Linux 需要 zenity 或 kdialog），且当前 Python 没有 tkinter。"
            "请直接在文本框填写绝对路径。"
        )
    proc = _run_command(build_tk_argv(title, initial or ""))
    return _interpret(proc, kind="tk").unwrap()


def _picker_env(title: str, initial: Optional[str]) -> dict[str, str]:
    env = os.environ.copy()
    env["AUTO_TAG_PICK_TITLE"] = title or "选择目录"
    env["AUTO_TAG_PICK_INITIAL"] = _initial_for_windows_dialog(initial)
    return env


def _initial_for_windows_dialog(initial: Optional[str]) -> str:
    """WSL 上把 /mnt/d/... 转成 D:\\...，否则 Windows 对话框无法定位初始目录。"""
    shown = initial or ""
    if not shown or not _is_wsl():
        return shown
    return posix_mount_to_windows_drive_path(shown) or shown


def _path_for_client(path: str) -> str:
    """WSL 上把校验用的 /mnt/d/... 还原成对话框返回的盘符路径。"""
    if not _is_wsl():
        return path
    return posix_mount_to_windows_drive_path(path) or path


def _resolve_powershell() -> str:
    if _is_wsl():
        return _resolve_wsl_powershell()
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = os.path.join(
        system_root,
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    if os.path.isfile(candidate):
        return candidate
    found = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
    if found:
        return found
    raise DirectoryDialogError("未找到 powershell.exe，无法打开 Windows 目录窗口")


def _resolve_wsl_powershell() -> str:
    """只用 Windows 的 powershell.exe。Linux 上的 pwsh 打不开系统「浏览文件夹」。"""
    found = shutil.which("powershell.exe")
    if found:
        return found
    mounted = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    if os.path.isfile(mounted):
        return mounted
    raise DirectoryDialogError(
        "未找到 powershell.exe，无法打开 Windows 目录窗口。请直接填写路径，例如 D:\\images。"
    )


def _tk_importable() -> bool:
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


@dataclass
class _Outcome:
    path: Optional[str]
    failed: bool = False

    def unwrap(self) -> Optional[str]:
        if self.failed:
            raise DirectoryDialogError(self.path or "无法打开目录选择窗口")
        return self.path


def _interpret(proc: subprocess.CompletedProcess[str], *, kind: str) -> _Outcome:
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    code = proc.returncode
    if code == 0:
        path = stdout.splitlines()[-1].strip() if stdout else ""
        if not path:
            return _Outcome(path="目录选择窗口未返回路径", failed=True)
        return _Outcome(path=path)
    if _is_display_error(stderr):
        return _Outcome(
            path="无法打开图形目录窗口（未检测到可用显示器）。请直接在文本框填写绝对路径。",
            failed=True,
        )
    if _looks_like_cancel(stderr) or code == 2 or (code == 1 and not stderr):
        return _Outcome(path=None)
    if kind in {"zenity", "kdialog", "osascript"} and code == 1 and not stdout:
        return _Outcome(path=None)
    message = stderr or f"目录选择失败（退出码 {code}）"
    return _Outcome(path=message, failed=True)


def _is_display_error(stderr: str) -> bool:
    text = stderr.lower()
    return (
        "cannot open display" in text
        or "unable to init server" in text
        or "no display" in text
    )


def _looks_like_cancel(stderr: str) -> bool:
    text = stderr.lower()
    return "user canceled" in text or "user cancelled" in text or "(-128)" in text


def _run_command(
    argv: list[str],
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict = {
        "args": argv,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    if env is not None:
        kwargs["env"] = env
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.run(**kwargs)
    except FileNotFoundError as exc:
        raise DirectoryDialogError(f"无法启动目录选择程序：{argv[0]}") from exc


def _tk_child_main(title: str, initial: str) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        sys.stderr.write(f"无法加载图形界面组件：{exc}\n")
        return 1
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    kwargs = {"title": title or "选择目录", "mustexist": True}
    if initial and os.path.isdir(initial):
        kwargs["initialdir"] = initial
    try:
        path = filedialog.askdirectory(**kwargs)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    if path:
        sys.stdout.write(str(path))
        return 0
    return 2


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="打开系统目录选择窗口（子进程）")
    parser.add_argument("--backend", choices=["tk"], required=True)
    parser.add_argument("--title", default="选择目录")
    parser.add_argument("--initial", default="")
    args = parser.parse_args(argv)
    return _tk_child_main(args.title, args.initial)


if __name__ == "__main__":
    raise SystemExit(main())
