import os
import subprocess
import threading
import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auto_tag.backend.export_path_utils import validate_export_directory
from auto_tag.backend.job_runner import is_busy, list_jobs
from auto_tag.constant import VERSION
from auto_tag.core.config import reload_settings_from_disk, settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    parent = os.path.dirname(os.path.abspath(settings.db_path))
    return {
        "status": "ok",
        "version": VERSION,
        "chroma_path": settings.db_path,
        "embedding_parent_exists": os.path.isdir(parent),
        "chroma_parent_exists": os.path.isdir(parent),
        "collection": settings.collection_name,
    }


class ReadFileParams(BaseModel):
    path: str = Field(..., description="文件绝对路径")


@router.get("/utils/read_file")
def read_file(path: str) -> Dict[str, Any]:
    """读取服务端文件内容（仅允许读取 auto_tag 目录树下的文本文件）。"""
    p = os.path.realpath(os.path.abspath(os.path.expanduser(path.strip())))
    # 安全限制：仅允许 auto_tag 目录下的文件
    allowed_prefix = os.path.realpath(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    if not p.startswith(allowed_prefix + os.sep) and not p.startswith(allowed_prefix):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be under {allowed_prefix}",
        )
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail=f"File not found: {p}")
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
        return {"path": p, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class WriteFileBody(BaseModel):
    path: str = Field(..., description="文件绝对路径")
    content: str = Field(..., description="写入内容")


@router.post("/utils/write_file")
def write_file(body: WriteFileBody) -> Dict[str, Any]:
    """写入内容到服务端文件（仅允许写入 auto_tag 目录树下）。"""
    p = os.path.realpath(os.path.abspath(os.path.expanduser(body.path.strip())))
    # 安全限制：仅允许 auto_tag 目录下的文件
    allowed_prefix = os.path.realpath(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    if not p.startswith(allowed_prefix + os.sep) and not p.startswith(allowed_prefix):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be under {allowed_prefix}",
        )
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body.content)
        return {"path": p, "written": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CheckDirsBody(BaseModel):
    dirs: List[str] = Field(..., description="待检查的目录路径列表")


class ValidateExportDirBody(BaseModel):
    path: str = Field(..., description="导出目标目录（后端所在机器上的绝对或 ~ 路径）")
    create_if_missing: bool = Field(
        default=False,
        description="目录不存在时是否尝试创建（需父目录可写）",
    )


@router.post("/utils/validate_export_dir")
def validate_export_dir(body: ValidateExportDirBody) -> Dict[str, Any]:
    """校验导出目录：存在性、类型、写权限、探针写入。"""
    return validate_export_directory(
        body.path,
        create_if_missing=bool(body.create_if_missing),
    )


@router.get("/utils/backend_status")
def backend_status() -> Dict[str, Any]:
    """返回后端是否繁忙及进行中的标注/维护任务。"""
    active = [
        j
        for j in list_jobs()
        if str(j.get("status", "")).lower() in ("running", "queued")
    ]
    return {
        "busy": is_busy(),
        "active_jobs": active,
        "active_job_count": len(active),
    }


@router.post("/utils/restart_backend")
def restart_backend() -> Dict[str, Any]:
    """重启后端进程，使磁盘 config.json 重新加载到内存。会中断进行中的任务。"""
    was_busy = is_busy()
    active_jobs = [
        j
        for j in list_jobs()
        if str(j.get("status", "")).lower() in ("running", "queued")
    ]

    routers_dir = os.path.dirname(os.path.abspath(__file__))
    auto_tag_pkg = os.path.dirname(os.path.dirname(routers_dir))
    repo_root = os.path.dirname(auto_tag_pkg)
    log_file = os.environ.get("AUTO_TAG_BACKEND_LOG", "/tmp/auto_tag_backend.log")

    import sys

    if sys.platform == "win32":
        script = os.path.join(repo_root, "scripts", "windows", "restart_web_backend.ps1")
        if not os.path.isfile(script):
            raise HTTPException(status_code=500, detail=f"Restart script not found: {script}")
        try:
            log_fp = open(log_file, "a", encoding="utf-8")
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Cannot open backend log: {e}") from e
        log_fp.close()
        try:
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    script,
                ],
                cwd=repo_root,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        script = os.path.join(repo_root, "scripts", "linux", "restart_web_backend.sh")
        if not os.path.isfile(script):
            raise HTTPException(status_code=500, detail=f"Restart script not found: {script}")

        try:
            log_fp = open(log_file, "a", encoding="utf-8")
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Cannot open backend log: {e}") from e

        try:
            subprocess.Popen(
                ["bash", script],
                cwd=repo_root,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
        except Exception as e:
            log_fp.close()
            raise HTTPException(status_code=500, detail=str(e)) from e
        log_fp.close()

    def _exit_process() -> None:
        # 留足时间把 HTTP 响应发回前端，再退出当前 uvicorn
        time.sleep(0.35)
        os._exit(0)

    threading.Thread(target=_exit_process, daemon=False).start()
    return {
        "ok": True,
        "restarting": True,
        "was_busy": was_busy,
        "active_job_count": len(active_jobs),
    }


@router.post("/utils/reload_config")
def reload_config() -> Dict[str, Any]:
    """在不重启进程的情况下，将磁盘 config 热加载到内存（进行中的任务仍可能使用旧参数）。"""
    try:
        reload_settings_from_disk()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "reloaded": True}


@router.post("/utils/check_dirs")
def check_dirs(body: CheckDirsBody) -> Dict[str, Any]:
    """检查输入的目录列表中哪些存在、哪些不存在，用于前端提交前校验。"""
    exist: List[str] = []
    not_exist: List[str] = []
    for d in body.dirs:
        p = os.path.realpath(os.path.abspath(os.path.expanduser(d.strip())))
        if os.path.isdir(p):
            exist.append(p)
        else:
            not_exist.append(d)  # 保留用户输入原文，方便前端定位
    return {"exist": exist, "not_exist": not_exist}
