"""数据库总览、导出、与构建快照比对（更新类操作为占位）。"""
from __future__ import annotations

import json
import logging
import numbers
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auto_tag.core.config import settings
from auto_tag.core.config_file_params import merge_stats_params_from_file
from auto_tag.core.db_build_snapshot import read_build_snapshot
from auto_tag.core.duplicate_store import read_duplicate_store
from auto_tag.core.pipeline import normalize_work_dir, work_chroma_dir, work_log_dir
from auto_tag.core.vector_db import VectorDB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/database", tags=["database"])

_EXPORT_MAX = 200_000


def _json_safe_response(obj: Any) -> Any:
    """将 stats 等接口返回值转为 FastAPI/JSON 可序列化类型（处理 numpy/Decimal 等）。"""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, numbers.Integral):
        return int(obj)
    if isinstance(obj, numbers.Real):
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe_response(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe_response(x) for x in obj]
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return _json_safe_response(obj.item())
        except Exception:
            pass
    return str(obj)


def _parse_labels_nonempty(meta: Optional[Dict[str, Any]]) -> bool:
    if not meta:
        return False
    raw = meta.get("labels_json")
    if raw is None:
        return False
    if isinstance(raw, dict):
        return bool(raw)
    s = str(raw).strip()
    if not s or s == "{}":
        return False
    try:
        d = json.loads(s)
        return bool(isinstance(d, dict) and d)
    except json.JSONDecodeError:
        return False


def _resolve_paths(work_dir: Optional[str]) -> Tuple[str, str, str]:
    """(work_root, embedding_store_path, log_dir)。"""
    if work_dir and str(work_dir).strip():
        wr = normalize_work_dir(work_dir)
        return wr, work_chroma_dir(wr), work_log_dir(wr)
    emb = os.path.realpath(
        os.path.abspath(os.path.expanduser(str(settings.db_path).strip()))
    )
    parent = os.path.dirname(emb.rstrip(os.sep))
    log_d = os.path.join(parent, "log") if parent else os.path.join(".", "log")
    wr = parent or os.getcwd()
    return wr, emb, log_d


def _scalar_differs(key: str, sv: Any, cv: Any) -> bool:
    if key in (
        "collection_name",
        "clip_model_name",
        "vlm_model_name",
        "duplicate_links_filename",
        "embedding_subdir",
        "embedding_store_path",
    ):
        return str(sv) != str(cv)
    try:
        return float(sv) != float(cv)
    except (TypeError, ValueError):
        return sv != cv


RELATION_DIFF_KEYS = (
    "tau_dup",
    "tau_cls",
    "batch_size",
    "collection_name",
    "clip_model_name",
    "vlm_model_name",
    "duplicate_links_filename",
    "embedding_subdir",
)


def _gather_embedding_items(
    db: VectorDB,
    *,
    mode: str,
    offset: int,
    limit: int,
    cluster_id: Optional[str],
    chunk_index: int,
    chunk_size: int,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if mode == "cluster":
        if not cluster_id or not str(cluster_id).strip():
            raise HTTPException(status_code=400, detail="cluster 模式需要 cluster_id")
        res = db.collection.get(
            where={"cluster_id": str(cluster_id).strip()},
            limit=100000,
            include=["metadatas"],
        )
        ids = res.get("ids") or []
        metas = res.get("metadatas") or []
        for i, doc_id in enumerate(ids):
            items.append(
                {"id": doc_id, "metadata": metas[i] if i < len(metas) else {}}
            )
    elif mode == "chunk":
        off = chunk_index * chunk_size
        res = db.collection.get(
            limit=chunk_size, offset=off, include=["metadatas"]
        )
        ids = res.get("ids") or []
        metas = res.get("metadatas") or []
        for i, doc_id in enumerate(ids):
            items.append(
                {"id": doc_id, "metadata": metas[i] if i < len(metas) else {}}
            )
    else:
        res = db.collection.get(
            limit=limit, offset=offset, include=["metadatas"]
        )
        ids = res.get("ids") or []
        metas = res.get("metadatas") or []
        for i, doc_id in enumerate(ids):
            items.append(
                {"id": doc_id, "metadata": metas[i] if i < len(metas) else {}}
            )
    return items


@router.get("/stats")
def database_stats(
    work_dir: Optional[str] = Query(None, description="工作根目录"),
    config_path: Optional[str] = Query(
        None,
        description="与 Streamlit「设置」中 config.json 路径一致时，用该文件覆盖「当前配置」比对快照",
    ),
) -> Dict[str, Any]:
    try:
        return _database_stats_impl(work_dir, config_path)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("database_stats failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _database_stats_impl(
    work_dir: Optional[str],
    config_path: Optional[str],
) -> Dict[str, Any]:
    wr, emb_path, log_dir = _resolve_paths(work_dir)
    dup_file = os.path.join(log_dir, settings.duplicate_links_filename)
    _, dup_total = read_duplicate_store(
        dup_file, limit=1, offset=0, log_dir=log_dir
    )

    snapshot = read_build_snapshot(log_dir)
    current_params: Dict[str, Any] = {
        "tau_dup": float(settings.tau_dup),
        "tau_cls": float(settings.tau_cls),
        "batch_size": int(settings.batch_size),
        "questions": dict(settings.questions or {}),
        "collection_name": str(settings.collection_name),
        "clip_model_name": str(settings.clip_model_name),
        "vlm_model_name": str(settings.vlm_model_name),
        "duplicate_links_filename": str(settings.duplicate_links_filename),
        "embedding_subdir": str(settings.embedding_subdir),
        "embedding_store_path": str(settings.db_path),
    }
    current_params = merge_stats_params_from_file(current_params, config_path)

    diff_rows: List[Dict[str, Any]] = []
    has_snapshot = snapshot is not None
    has_relation_diff = False
    has_questions_diff = False
    if not has_snapshot:
        diff_rows.append(
            {
                "参数": "(无快照文件)",
                "数据库快照(上次任务)": "未找到 auto_tag_db_build_snapshot.json（尚未成功跑完任务或旧数据）",
                "当前配置": "—",
            }
        )
    else:
        for key in RELATION_DIFF_KEYS:
            sv = snapshot.get(key)
            cv = current_params.get(key)
            if _scalar_differs(key, sv, cv):
                has_relation_diff = True
                diff_rows.append(
                    {"参数": key, "数据库快照(上次任务)": sv, "当前配置": cv}
                )
        sq = json.dumps(snapshot.get("questions") or {}, sort_keys=True, ensure_ascii=True)
        cq = json.dumps(current_params["questions"], sort_keys=True, ensure_ascii=True)
        if sq != cq:
            has_questions_diff = True
            diff_rows.append(
                {
                    "参数": "questions",
                    "数据库快照(上次任务)": "(见 snapshot.questions)",
                    "当前配置": "(见 current_params.questions)",
                }
            )

    has_config_diff = has_relation_diff or has_questions_diff

    emb_count = 0
    cluster_ids: set[str] = set()
    labeled_count = 0
    if os.path.isdir(emb_path):
        try:
            db = VectorDB(db_path=emb_path, collection_name=settings.collection_name)
            emb_count = db.count()
            offset = 0
            batch = 2500
            while offset < emb_count:
                res = db.collection.get(
                    limit=min(batch, emb_count - offset),
                    offset=offset,
                    include=["metadatas"],
                )
                metas = res.get("metadatas") or []
                for m in metas:
                    if not m:
                        continue
                    cid = m.get("cluster_id")
                    if cid:
                        cluster_ids.add(str(cid))
                    if _parse_labels_nonempty(m):
                        labeled_count += 1
                offset += len(res.get("ids") or [])
                if not res.get("ids"):
                    break
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    payload: Dict[str, Any] = {
        "work_dir": wr,
        "embedding_store_path": emb_path,
        "chroma_path": emb_path,
        "log_dir": log_dir,
        "embedding_record_count": emb_count,
        "chroma_document_count": emb_count,
        "cluster_count": len(cluster_ids),
        "labeled_document_count": labeled_count,
        "duplicate_link_rows": dup_total,
        "snapshot": snapshot,
        "current_params": current_params,
        "config_path_effective": (
            os.path.abspath(os.path.expanduser(str(config_path).strip()))
            if config_path and str(config_path).strip()
            else None
        ),
        "has_snapshot": has_snapshot,
        "has_config_diff": has_config_diff,
        "has_relation_diff": has_relation_diff,
        "has_questions_diff": has_questions_diff,
        "enable_recompute_relations": bool(has_snapshot and has_relation_diff),
        "enable_rebuild_relations": True,
        "enable_reannotate": bool(has_snapshot and has_questions_diff),
        "param_diff_table": diff_rows,
    }
    return _json_safe_response(payload)


@router.get("/export_embeddings")
def export_embeddings(
    work_dir: Optional[str] = Query(None),
    mode: str = Query("range", description="range | cluster | chunk"),
    offset: int = Query(0, ge=0),
    limit: int = Query(_EXPORT_MAX, ge=1, le=_EXPORT_MAX),
    cluster_id: Optional[str] = Query(None),
    chunk_index: int = Query(0, ge=0),
    chunk_size: int = Query(_EXPORT_MAX, ge=1, le=_EXPORT_MAX),
) -> Response:
    """仅导出向量索引中的记录（embedding_records）。"""
    wr, emb_path, log_dir = _resolve_paths(work_dir)
    if not os.path.isdir(emb_path):
        raise HTTPException(status_code=400, detail="向量索引目录不存在")

    db = VectorDB(db_path=emb_path, collection_name=settings.collection_name)
    items = _gather_embedding_items(
        db,
        mode=mode,
        offset=offset,
        limit=limit,
        cluster_id=cluster_id,
        chunk_index=chunk_index,
        chunk_size=chunk_size,
    )

    payload: Dict[str, Any] = {
        "export_meta": {
            "work_dir": wr,
            "embedding_store_path": emb_path,
            "log_dir": log_dir,
            "resource": "embedding_records",
            "mode": mode,
            "offset": offset,
            "limit": limit,
            "cluster_id": cluster_id,
            "chunk_index": chunk_index,
            "chunk_size": chunk_size,
            "record_count": len(items),
        },
        "embedding_records": items,
    }

    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="auto_tag_embeddings_{mode}.json"'
        },
    )


@router.get("/export_duplicates")
def export_duplicates(
    work_dir: Optional[str] = Query(None),
    mode: str = Query("range", description="range | chunk"),
    offset: int = Query(0, ge=0),
    limit: int = Query(_EXPORT_MAX, ge=1, le=_EXPORT_MAX),
    chunk_index: int = Query(0, ge=0),
    chunk_size: int = Query(_EXPORT_MAX, ge=1, le=_EXPORT_MAX),
) -> Response:
    """仅导出近重复侧车 duplicate_links。"""
    wr, emb_path, log_dir = _resolve_paths(work_dir)
    dup_file = os.path.join(log_dir, settings.duplicate_links_filename)
    _, dtotal = read_duplicate_store(
        dup_file, limit=1, offset=0, log_dir=log_dir
    )

    if mode == "chunk":
        off = chunk_index * chunk_size
        lim = chunk_size
    else:
        off = offset
        lim = limit

    rows, _ = read_duplicate_store(
        dup_file, limit=lim, offset=off, log_dir=log_dir
    )
    payload: Dict[str, Any] = {
        "export_meta": {
            "work_dir": wr,
            "embedding_store_path": emb_path,
            "log_dir": log_dir,
            "resource": "duplicate_links",
            "mode": mode,
            "offset": off,
            "limit": lim,
            "chunk_index": chunk_index if mode == "chunk" else None,
            "chunk_size": chunk_size if mode == "chunk" else None,
            "row_count": len(rows),
            "duplicate_total": dtotal,
        },
        "duplicate_links": rows,
    }
    if dtotal > off + len(rows):
        payload["export_meta"]["note"] = (
            f"本文件共 {len(rows)} 条，侧车总计约 {dtotal} 条，可继续增大 offset 或下一 chunk。"
        )

    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="auto_tag_duplicates_{mode}.json"'
        },
    )


class OptionalWorkDirBody(BaseModel):
    work_dir: Optional[str] = None


class ReannotateBody(BaseModel):
    work_dir: Optional[str] = None
    full_refresh: bool = Field(default=False, description="全量：按当前 questions 整图重标并覆盖")
    incremental: bool = Field(default=False, description="增量：仅为缺失的 question 键调用 VLM")
    centers_only: bool = Field(
        default=False, description="仅对簇中心图调 VLM（可与全量/增量组合）"
    )


@router.get("/export_compact_shared")
def export_compact_shared(work_dir: Optional[str] = Query(None)) -> Response:
    """紧凑导出：共享字典（labels / prefix / cluster / cluster_to_labels）。"""
    from auto_tag.core.compact_labels_export import (
        build_compact_export,
        shared_compact_dict,
    )

    wr, _, _ = _resolve_paths(work_dir)
    try:
        full = build_compact_export(wr)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    payload = shared_compact_dict(full)
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="auto_tag_compact_labels_shared.json"'
        },
    )


@router.get("/export_compact_slice")
def export_compact_slice(
    work_dir: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(_EXPORT_MAX, ge=1, le=_EXPORT_MAX),
) -> Response:
    """紧凑导出：平行字段切片（images / labels_id / prefix_id / cluster_id）。"""
    from auto_tag.core.compact_labels_export import (
        build_compact_export,
        slice_compact_parallel,
    )

    wr, _, _ = _resolve_paths(work_dir)
    try:
        full = build_compact_export(wr)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    payload = slice_compact_parallel(full, offset=offset, limit=limit)
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="auto_tag_compact_slice_{offset}_{limit}.json"'
        },
    )


@router.get("/export_compact_chunk")
def export_compact_chunk(
    work_dir: Optional[str] = Query(None),
    chunk_index: int = Query(0, ge=0),
    chunk_size: int = Query(_EXPORT_MAX, ge=1, le=_EXPORT_MAX),
) -> Response:
    """紧凑导出：按块切平行字段。"""
    from auto_tag.core.compact_labels_export import (
        build_compact_export,
        slice_compact_chunk,
    )

    wr, _, _ = _resolve_paths(work_dir)
    try:
        full = build_compact_export(wr)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    payload = slice_compact_chunk(
        full, chunk_index=chunk_index, chunk_size=chunk_size
    )
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="auto_tag_compact_chunk_{chunk_index}.json"'
        },
    )


@router.post("/recompute_relations")
def recompute_relations(body: OptionalWorkDirBody = OptionalWorkDirBody()) -> Dict[str, Any]:
    """复用索引中已有向量与 labels，仅按当前 τ_dup/τ_cls 重算簇与侧车（不调用 VLM/CLIP）。"""
    from auto_tag.backend.job_runner import run_exclusive_task
    from auto_tag.core.database_maintenance import recompute_relations_only

    wr, _, _ = _resolve_paths(body.work_dir)
    try:
        return run_exclusive_task(lambda: recompute_relations_only(wr))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/rebuild_relations")
def rebuild_relations(body: OptionalWorkDirBody = OptionalWorkDirBody()) -> Dict[str, Any]:
    """完全重建索引：清空后按快照 input_dirs 重跑完整流水线（CLIP + VLM 等）。"""
    from auto_tag.backend.job_runner import run_exclusive_task
    from auto_tag.core.database_maintenance import (
        rebuild_relations as run_rebuild_relations,
    )

    wr, _, _ = _resolve_paths(body.work_dir)
    try:
        return run_exclusive_task(lambda: run_rebuild_relations(wr))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/reannotate")
def reannotate(body: ReannotateBody) -> Dict[str, Any]:
    """按全量或增量策略批量调用 VLM 更新 labels_json。"""
    from auto_tag.backend.job_runner import run_exclusive_task
    from auto_tag.core.database_maintenance import reannotate as run_reannotate

    if body.full_refresh and body.incremental:
        raise HTTPException(status_code=400, detail="全量与增量互斥，请勿同时勾选")
    if not body.full_refresh and not body.incremental:
        raise HTTPException(status_code=400, detail="请选择全量或增量之一")
    wr, _, _ = _resolve_paths(body.work_dir)
    ff = bool(body.full_refresh)
    inc = bool(body.incremental)
    co = bool(body.centers_only)
    try:
        return run_exclusive_task(
            lambda: run_reannotate(
                wr, full_refresh=ff, incremental=inc, centers_only=co
            )
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
