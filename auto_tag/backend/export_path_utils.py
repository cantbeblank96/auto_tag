"""导出目录校验与落盘（后端进程所在机器上的本地路径）。"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

_PROBE_BASENAME = ".auto_tag_export_write_probe"


def normalize_export_dir(path_str: str) -> str:
    """展开 ~ 并转为绝对 realpath。"""
    raw = str(path_str or "").strip()
    if not raw:
        raise ValueError("路径不能为空")
    return os.path.realpath(os.path.abspath(os.path.expanduser(raw)))


def validate_export_directory(
    path_str: str,
    *,
    create_if_missing: bool = False,
) -> Dict[str, Any]:
    """
    校验导出目录：存在性、类型、可写性、探针写入。
    返回结构化检查结果，供前端展示。
    """
    checks: List[Dict[str, Any]] = []
    input_path = str(path_str or "").strip()
    if not input_path:
        checks.append({"name": "非空", "passed": False, "message": "请填写目录路径"})
        return {
            "ok": False,
            "input_path": input_path,
            "path": None,
            "exists": False,
            "is_directory": False,
            "writable": False,
            "probe_write_ok": False,
            "created": False,
            "checks": checks,
            "message": "路径不能为空",
        }

    try:
        resolved = normalize_export_dir(input_path)
    except ValueError as e:
        checks.append({"name": "路径解析", "passed": False, "message": str(e)})
        return {
            "ok": False,
            "input_path": input_path,
            "path": None,
            "exists": False,
            "is_directory": False,
            "writable": False,
            "probe_write_ok": False,
            "created": False,
            "checks": checks,
            "message": str(e),
        }

    checks.append(
        {
            "name": "路径解析",
            "passed": True,
            "message": f"解析为 {resolved}",
        }
    )

    created = False
    exists = os.path.exists(resolved)
    if exists:
        is_dir = os.path.isdir(resolved)
        checks.append(
            {
                "name": "目录存在",
                "passed": is_dir,
                "message": "已是目录" if is_dir else "路径存在但不是目录（请改为文件夹路径）",
            }
        )
        if not is_dir:
            return _result(
                ok=False,
                input_path=input_path,
                path=resolved,
                exists=True,
                is_directory=False,
                writable=False,
                probe_write_ok=False,
                created=False,
                checks=checks,
                message="路径存在但不是目录",
            )
    else:
        checks.append(
            {
                "name": "目录存在",
                "passed": False,
                "message": "目录尚不存在",
            }
        )
        if create_if_missing:
            try:
                os.makedirs(resolved, exist_ok=True)
                created = True
                exists = True
                checks.append(
                    {
                        "name": "自动创建",
                        "passed": True,
                        "message": f"已创建目录 {resolved}",
                    }
                )
            except OSError as e:
                checks.append(
                    {
                        "name": "自动创建",
                        "passed": False,
                        "message": f"创建失败: {e}",
                    }
                )
                return _result(
                    ok=False,
                    input_path=input_path,
                    path=resolved,
                    exists=False,
                    is_directory=False,
                    writable=False,
                    probe_write_ok=False,
                    created=False,
                    checks=checks,
                    message=f"无法创建目录: {e}",
                )
        else:
            parent = os.path.dirname(resolved)
            parent_ok = os.path.isdir(parent) and os.access(parent, os.W_OK)
            checks.append(
                {
                    "name": "父目录可写",
                    "passed": parent_ok,
                    "message": (
                        f"父目录 {parent} 可写，可勾选「不存在则创建」"
                        if parent_ok
                        else f"父目录 {parent} 不存在或不可写"
                    ),
                }
            )
            return _result(
                ok=False,
                input_path=input_path,
                path=resolved,
                exists=False,
                is_directory=False,
                writable=False,
                probe_write_ok=False,
                created=False,
                checks=checks,
                message="目录不存在；可验证时勾选「不存在则创建」",
            )

    writable = os.access(resolved, os.W_OK)
    checks.append(
        {
            "name": "目录可写",
            "passed": writable,
            "message": "当前进程对该目录有写权限" if writable else "目录不可写",
        }
    )
    if not writable:
        return _result(
            ok=False,
            input_path=input_path,
            path=resolved,
            exists=exists,
            is_directory=True,
            writable=False,
            probe_write_ok=False,
            created=created,
            checks=checks,
            message="目录不可写",
        )

    probe_ok = False
    probe_path = os.path.join(resolved, f"{_PROBE_BASENAME}_{uuid.uuid4().hex}")
    try:
        with open(probe_path, "w", encoding="utf-8") as f:
            f.write("ok")
        probe_ok = True
        os.remove(probe_path)
        checks.append(
            {
                "name": "探针写入",
                "passed": True,
                "message": "测试文件写入并删除成功",
            }
        )
    except OSError as e:
        checks.append(
            {
                "name": "探针写入",
                "passed": False,
                "message": f"写入测试失败: {e}",
            }
        )
        return _result(
            ok=False,
            input_path=input_path,
            path=resolved,
            exists=exists,
            is_directory=True,
            writable=writable,
            probe_write_ok=False,
            created=created,
            checks=checks,
            message=f"探针写入失败: {e}",
        )

    return _result(
        ok=True,
        input_path=input_path,
        path=resolved,
        exists=exists,
        is_directory=True,
        writable=writable,
        probe_write_ok=probe_ok,
        created=created,
        checks=checks,
        message="目录可用于导出",
    )


def _result(
    *,
    ok: bool,
    input_path: str,
    path: str | None,
    exists: bool,
    is_directory: bool,
    writable: bool,
    probe_write_ok: bool,
    created: bool,
    checks: List[Dict[str, Any]],
    message: str,
) -> Dict[str, Any]:
    return {
        "ok": ok,
        "input_path": input_path,
        "path": path,
        "exists": exists,
        "is_directory": is_directory,
        "writable": writable,
        "probe_write_ok": probe_write_ok,
        "created": created,
        "checks": checks,
        "message": message,
    }


def save_export_file(output_dir: str, filename: str, data: bytes) -> Dict[str, Any]:
    """将导出内容写入 output_dir/filename（写入前再次校验目录）。"""
    safe_name = os.path.basename(str(filename or "").strip())
    if not safe_name or safe_name in (".", ".."):
        raise ValueError("无效的文件名")

    v = validate_export_directory(output_dir, create_if_missing=False)
    if not v.get("ok"):
        raise ValueError(str(v.get("message") or "导出目录无效"))

    dir_path = str(v["path"])
    out_path = os.path.join(dir_path, safe_name)
    if os.path.exists(out_path) and not os.path.isfile(out_path):
        raise ValueError(f"目标路径已存在且不是文件: {out_path}")

    with open(out_path, "wb") as f:
        f.write(data)

    return {
        "ok": True,
        "saved": True,
        "path": out_path,
        "directory": dir_path,
        "filename": safe_name,
        "bytes": len(data),
    }
