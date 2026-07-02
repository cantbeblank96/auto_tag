import os
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auto_tag.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    parent = os.path.dirname(os.path.abspath(settings.db_path))
    return {
        "status": "ok",
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
