import os
import re
import sys
from typing import List, Optional


_WIN_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_POSIX_MNT_RE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")


def windows_drive_path_to_posix_mount(path: str) -> Optional[str]:
    """若为 Windows 盘符绝对路径（如 D:\\foo 或 D:/foo），转为 /mnt/<盘符>/foo；否则返回 None。"""
    s = (path or "").strip()
    if not s:
        return None
    m = _WIN_DRIVE_RE.match(s)
    if not m:
        return None
    drive = m.group(1).lower()
    rest = (m.group(2) or "").replace("\\", "/").strip("/")
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def posix_mount_to_windows_drive_path(path: str) -> Optional[str]:
    """若为 WSL 盘符挂载路径（/mnt/d/...），转为 D:\\...；否则返回 None。"""
    s = (path or "").strip().replace("\\", "/")
    if not s:
        return None
    m = _POSIX_MNT_RE.match(s)
    if not m:
        return None
    drive = m.group(1).upper()
    rest = (m.group(2) or "").replace("/", "\\").strip("\\")
    return f"{drive}:\\{rest}" if rest else f"{drive}:\\"


def normalize_fs_path(path: str) -> str:
    """
    将用户输入路径规范为当前主机可访问的绝对路径。

    - Linux/WSL：把 D:\\foo、D:/foo 转为 /mnt/d/foo（再 expanduser/abspath/realpath）
    - Windows：把 /mnt/d/foo 转为 D:\\foo
    - 空串保持为空
    """
    s = (path or "").strip()
    if not s:
        return ""
    if sys.platform == "win32":
        converted = posix_mount_to_windows_drive_path(s)
        if converted is not None:
            s = converted
    else:
        converted = windows_drive_path_to_posix_mount(s)
        if converted is not None:
            s = converted
    return os.path.realpath(os.path.abspath(os.path.expanduser(s)))


def path_variants(p: str) -> List[str]:
    """返回输入路径及其 realpath / 跨平台挂载变体，用于模糊匹配。"""
    s = (p or "").strip()
    if not s:
        return []
    out: List[str] = [s]
    # 互转变体（不依赖当前 OS，便于索引里混存两种写法）
    for alt in (
        windows_drive_path_to_posix_mount(s),
        posix_mount_to_windows_drive_path(s),
    ):
        if alt and alt not in out:
            out.append(alt)
    try:
        r = normalize_fs_path(s)
        if r and r not in out:
            out.append(r)
    except OSError:
        pass
    return out
