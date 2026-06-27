import json
import os
import uuid
from io import BytesIO
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auto_tag.backend.public_json import public_chroma_metadata, public_duplicate_row
from auto_tag.core.annotator import ImageAutoAnnotator
from auto_tag.core.config import settings
from auto_tag.core.duplicate_store import find_duplicate_links_for_paths
from auto_tag.core.feature_extractor import FeatureExtractor
from auto_tag.core.path_prefix_registry import PathPrefixRegistry, resolve_stored_image_path
from auto_tag.core.pipeline import (
    PipelineConfig,
    decode_meta_for_path,
    normalize_work_dir,
    work_chroma_dir,
    work_log_dir,
)
from auto_tag.core.utils.load_image import load_image_for_job
from auto_tag.core.vector_db import VectorDB

router = APIRouter(prefix="/records", tags=["records"])


def _open_vector_db(work_dir: Optional[str], output_dir: Optional[str]) -> Tuple[VectorDB, str]:
    db_path = _resolve_records_db_path(work_dir, output_dir)
    if not os.path.isdir(db_path):
        raise HTTPException(
            status_code=400,
            detail=f"向量索引目录不存在: {db_path}",
        )
    return VectorDB(db_path=db_path, collection_name=settings.collection_name), db_path


def _is_cluster_center(meta: Optional[Dict[str, Any]]) -> bool:
    if not meta:
        return False
    v = meta.get("is_cluster_center")
    return v is True or str(v).lower() in ("true", "1")


def _generate_unique_cluster_id(db: VectorDB) -> str:
    while True:
        new_id = f"cls_{uuid.uuid4().hex[:8]}"
        existing = db.collection.get(where={"cluster_id": new_id}, limit=1)
        if not existing.get("ids"):
            return new_id


def _parse_labels_json(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not meta:
        return {}
    raw = meta.get("labels_json")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return {}


def _resolve_records_db_path(work_dir: Optional[str], output_dir: Optional[str]) -> str:
    """与 pipeline 一致：work_dir 下为 embedding_subdir；未指定则用 config 的 embedding_store_path。"""
    root = (work_dir or output_dir or "").strip()
    if root:
        return work_chroma_dir(root)
    return os.path.realpath(
        os.path.abspath(os.path.expanduser(str(settings.db_path).strip()))
    )


def _embedding_hit_payload(
    db: VectorDB,
    variants: List[str],
    *,
    chroma_path: str,
    work_dir: Optional[str] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """若命中向量库，返回 (matched_path, 详情 dict，不含路径字段)。"""
    reg = _registry_for_chroma(chroma_path, work_dir)
    ids: List[str] = []
    metas: List[Dict[str, Any]] = []
    matched_key = ""
    for cand in variants:
        ids, metas = db.get_by_image_path(cand, limit=20, registry=reg)
        if ids:
            matched_key = cand
            break
    if not ids or not metas:
        return None
    primary = metas[0]
    cid = str(primary.get("cluster_id") or "")
    _, c_metas = db.get_cluster_members(cid) if cid else ([], [])
    center_meta: Optional[Dict[str, Any]] = None
    for m in c_metas:
        if m and _is_cluster_center(m):
            center_meta = m
            break
    center_labels = _parse_labels_json(center_meta)
    own_labels = _parse_labels_json(primary)
    effective = own_labels if own_labels else center_labels
    matched_resolved = (
        resolve_stored_image_path(primary, reg)
        if reg
        else str(primary.get("image_path") or "")
    )
    cc_path = ""
    if center_meta:
        cc_path = (
            resolve_stored_image_path(center_meta, reg)
            if reg
            else str(center_meta.get("image_path") or "")
        )
    body: Dict[str, Any] = {
        "found": True,
        "source": "embedding_index",
        "matched_path": matched_resolved or matched_key,
        "doc_ids": ids,
        "records": [
            {"id": i, "metadata": public_chroma_metadata(m, registry=reg)}
            for i, m in zip(ids, metas)
        ],
        "cluster_id": cid,
        "is_cluster_center": _is_cluster_center(primary),
        "cluster_center_path": cc_path or None,
        "cluster_center_labels": center_labels,
        "own_labels": own_labels,
        "effective_labels": effective,
    }
    return matched_key, body


@router.get("")
def list_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    cluster_id: Optional[str] = None,
    work_dir: Optional[str] = Query(
        None,
        description="工作根目录；不传则使用服务端 config 中的 embedding_store_path",
    ),
    output_dir: Optional[str] = Query(
        None,
        description="已废弃，等同于 work_dir",
    ),
) -> Dict[str, Any]:
    where = {"cluster_id": cluster_id} if cluster_id else None
    db_path = _resolve_records_db_path(work_dir, output_dir)
    if not os.path.isdir(db_path):
        return {
            "total": 0,
            "offset": offset,
            "limit": limit,
            "items": [],
            "embedding_store_path": db_path,
            "chroma_path": db_path,
            "note": "向量索引目录不存在。请与「设置」中的 work_dir 一致，或先跑任务生成索引数据。",
        }
    try:
        db = VectorDB(db_path=db_path, collection_name=settings.collection_name)
        n = db.count()
        if n == 0:
            return {
                "total": 0,
                "offset": offset,
                "limit": limit,
                "items": [],
                "embedding_store_path": db_path,
                "chroma_path": db_path,
            }
        get_kw: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "include": ["metadatas"],
        }
        if where is not None:
            get_kw["where"] = where
        res = db.collection.get(**get_kw)
        metas: List[Optional[Dict[str, Any]]] = res.get("metadatas") or []
        reg = _registry_for_chroma(db_path, work_dir)
        items = [
            public_chroma_metadata(m, registry=reg) for m in metas if m
        ]
        total_out = n if cluster_id is None else None
        return {
            "total": total_out,
            "offset": offset,
            "limit": limit,
            "items": items,
            "embedding_store_path": db_path,
            "chroma_path": db_path,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/safe_path_check")
def safe_path_check(path: str) -> Dict[str, Any]:
    """用于缩略图等：仅检查路径是否存在且为文件（后续可加白名单）。"""
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail="File not found")
    return {"path": p, "ok": True}


def _registry_for_chroma(
    chroma_path: str, work_dir: Optional[str] = None
) -> Optional[PathPrefixRegistry]:
    if work_dir and str(work_dir).strip():
        ld = work_log_dir(normalize_work_dir(work_dir))
    else:
        ld = _infer_log_dir_from_chroma(chroma_path) or ""
    if ld and os.path.isdir(ld):
        return PathPrefixRegistry(ld)
    return None


def _infer_log_dir_from_chroma(chroma_path: str) -> Optional[str]:
    """在父目录下查找 log（标准布局为 work_dir/chroma_data + work_dir/log）。

    即使 chroma 目录名不是 chroma_data（例如 config 指向自定义路径），仍假定
    duplicate_links 与 chroma 位于同一工作根下的 log/，即 parent(chroma)/log。
    """
    try:
        p = os.path.realpath(chroma_path)
    except OSError:
        p = chroma_path
    parent = os.path.dirname(p.rstrip(os.sep))
    if not parent:
        return None
    return os.path.join(parent, "log")


def _path_variants(p: str) -> List[str]:
    s = (p or "").strip()
    if not s:
        return []
    out = [s]
    try:
        r = os.path.realpath(os.path.abspath(os.path.expanduser(s)))
        if r not in out:
            out.append(r)
    except OSError:
        pass
    return out


@router.get("/by_path")
def record_by_path(
    image_path: str = Query(..., description="与入库时一致的绝对路径（或等价 realpath）"),
    work_dir: Optional[str] = Query(None),
    output_dir: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """先查向量索引；若无记录再查 Stage1 侧车 duplicate_links（可能仅登记在侧车）。"""
    emb_path = _resolve_records_db_path(work_dir, output_dir)
    variants = _path_variants(image_path)

    if os.path.isdir(emb_path):
        try:
            db = VectorDB(db_path=emb_path, collection_name=settings.collection_name)
            hit = _embedding_hit_payload(
                db, variants, chroma_path=emb_path, work_dir=work_dir
            )
            if hit:
                _, body = hit
                return {
                    **body,
                    "embedding_store_path": emb_path,
                    "chroma_path": emb_path,
                }
        except Exception:
            pass

    log_dir: Optional[str] = None
    if (work_dir or output_dir or "").strip():
        log_dir = work_log_dir((work_dir or output_dir or "").strip())
    else:
        log_dir = _infer_log_dir_from_chroma(emb_path)

    dup_rows: List[Dict[str, Any]] = []
    if log_dir:
        dup_file = os.path.join(log_dir, settings.duplicate_links_filename)
        dup_rows = find_duplicate_links_for_paths(
            dup_file, variants, log_dir=log_dir, limit=200
        )

    if dup_rows:
        hit = variants[0]
        for row in dup_rows:
            if row.get("dup_path") in variants:
                hit = row.get("dup_path")
                break
            if row.get("anchor_path") in variants:
                hit = row.get("anchor_path")
                break
        anchor_records: List[Dict[str, Any]] = []
        if os.path.isdir(emb_path):
            try:
                qdb = VectorDB(db_path=emb_path, collection_name=settings.collection_name)
                seen_anchor: set[str] = set()
                for row in dup_rows:
                    ap = str(row.get("anchor_path") or "").strip()
                    if not ap or ap in seen_anchor:
                        continue
                    seen_anchor.add(ap)
                    ah = _embedding_hit_payload(
                        qdb,
                        _path_variants(ap),
                        chroma_path=emb_path,
                        work_dir=work_dir,
                    )
                    if ah:
                        _, abody = ah
                        anchor_records.append(
                            {
                                "anchor_path": ap,
                                "embedding_record": {
                                    **abody,
                                    "embedding_store_path": emb_path,
                                    "chroma_path": emb_path,
                                },
                            }
                        )
            except Exception:
                pass
        return {
            "found": True,
            "source": "stage1_duplicate_only",
            "embedding_store_path": emb_path,
            "chroma_path": emb_path,
            "matched_path": hit,
            "duplicate_links": [public_duplicate_row(r) for r in dup_rows],
            "anchor_embedding_records": anchor_records,
            "note": (
                "该路径未写入向量索引（多为近重复帧），仅在侧车 duplicate_links 中有记录；"
                "下方已自动列出锚点图在索引中的查询结果（若有）。"
            ),
        }

    return {
        "found": False,
        "embedding_store_path": emb_path,
        "chroma_path": emb_path,
        "image_path": image_path.strip(),
    }


@router.get("/preview")
def preview_image(
    image_path: str = Query(...),
    work_dir: Optional[str] = Query(None),
    output_dir: Optional[str] = Query(None),
    image_width: int = Query(0, ge=0),
    image_height: int = Query(0, ge=0),
    yuv_type: str = Query("nv21"),
    b_yuv_image: bool = Query(False),
    mixed_yuv: bool = Query(False),
    rotate_angle: Optional[str] = Query(None),
) -> Response:
    """将磁盘上的图片解码为 PNG（优先使用库中保存的 YUV 元数据）。"""
    p = os.path.realpath(os.path.abspath(os.path.expanduser(image_path.strip())))
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail="File not found")

    db, chroma_path = _open_vector_db(work_dir, output_dir)
    reg = _registry_for_chroma(chroma_path, work_dir)
    b_yuv = b_yuv_image
    mixed = mixed_yuv
    yw, yh = image_width, image_height
    yt = yuv_type
    for cand in _path_variants(p):
        ids, metas = db.get_by_image_path(cand, limit=1, registry=reg)
        if ids and metas and metas[0]:
            m = metas[0]
            if str(m.get("media_kind") or "") == "yuv":
                b_yuv = True
                mixed = False
                yw = int(m.get("pix_w") or 0)
                yh = int(m.get("pix_h") or 0)
                yt = str(m.get("yuv_layout") or "nv21")
            break

    try:
        img = load_image_for_job(
            p,
            b_yuv_image=b_yuv,
            mixed_yuv=mixed,
            yuv_type=yt,
            image_height=yh,
            image_width=yw,
            rotate_angle=rotate_angle,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    buf = BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


class UpdateLabelsBody(BaseModel):
    work_dir: Optional[str] = None
    image_path: str = Field(..., description="要更新的图片路径（与库中 image_path 一致或等价 realpath）")
    labels: Dict[str, Any] = Field(default_factory=dict)
    mode: Literal["with_cluster", "image_only"] = Field(
        default="image_only",
        description=(
            "with_cluster：该图所在簇内全部文档统一为同一 labels；"
            "image_only：只改该路径对应文档；若无库记录则插入新向量条目（需磁盘可读且可传 YUV 参数）"
        ),
    )
    # 无库记录插入新条目时用于解码（与流水线一致）
    image_width: int = Field(default=0, ge=0)
    image_height: int = Field(default=0, ge=0)
    yuv_type: str = "nv21"
    b_yuv_image: bool = False
    mixed_yuv: bool = False
    rotate_angle: Optional[str] = None


@router.post("/update_labels")
def update_labels(body: UpdateLabelsBody) -> Dict[str, Any]:
    """with_cluster：更新该图及其所属 cluster 内全部文档的 labels；image_only：仅更新该图对应文档；若无该路径记录则 CLIP 提特征后新增一条带 labels 的条目。"""
    db, chroma_path = _open_vector_db(body.work_dir, None)
    reg = _registry_for_chroma(chroma_path, body.work_dir)
    labels_json = json.dumps(body.labels, ensure_ascii=False)
    matched_ids: List[str] = []
    matched_metas: List[Dict[str, Any]] = []
    for cand in _path_variants(body.image_path):
        matched_ids, matched_metas = db.get_by_image_path(
            cand, limit=100, registry=reg
        )
        if matched_ids:
            break

    if body.mode == "image_only":
        if matched_ids:
            updated = 0
            for doc_id, meta in zip(matched_ids, matched_metas):
                if not meta:
                    continue
                new_meta = {**meta, "labels_json": labels_json}
                db.collection.update(ids=[doc_id], metadatas=[new_meta])
                updated += 1
            return {
                "ok": True,
                "mode": body.mode,
                "action": "update",
                "updated": updated,
                "chroma_path": chroma_path,
            }

        # 库中无该路径（例如 Stage1 重复未入库）：插入新文档
        p = os.path.realpath(os.path.abspath(os.path.expanduser(body.image_path.strip())))
        if not os.path.isfile(p):
            raise HTTPException(
                status_code=404,
                detail="No Chroma record for this path and file not found on server for insert",
            )
        cfg = PipelineConfig(
            b_yuv_image=body.b_yuv_image,
            mixed_yuv=body.mixed_yuv,
            yuv_type=body.yuv_type,
            image_width=body.image_width,
            image_height=body.image_height,
            rotate_angle=body.rotate_angle,
        )
        try:
            img = load_image_for_job(
                p,
                b_yuv_image=cfg.b_yuv_image,
                mixed_yuv=cfg.mixed_yuv,
                yuv_type=cfg.yuv_type,
                image_height=cfg.image_height,
                image_width=cfg.image_width,
                rotate_angle=cfg.rotate_angle,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to load image: {e}") from e

        extractor = FeatureExtractor(
            model_name=settings.clip_model_name,
            device=settings.device,
        )
        embeddings = extractor.extract_features_batch([img])
        if not embeddings:
            raise HTTPException(status_code=500, detail="Feature extraction returned empty")
        cluster_id = _generate_unique_cluster_id(db)
        dm = decode_meta_for_path(p, cfg)
        meta = ImageAutoAnnotator._row_meta(
            p,
            cluster_id,
            True,
            labels_json,
            dm,
            path_prefix_registry=reg,
        )
        doc_id = str(uuid.uuid4())
        db.add_batch([doc_id], [embeddings[0]], [meta])
        return {
            "ok": True,
            "mode": body.mode,
            "action": "insert",
            "doc_id": doc_id,
            "cluster_id": cluster_id,
            "chroma_path": chroma_path,
        }

    # with_cluster: 取簇 id，更新簇内全部文档
    if not matched_ids:
        raise HTTPException(
            status_code=404,
            detail="No record for image_path (with_cluster requires an existing record)",
        )
    primary = matched_metas[0] or {}
    cid = str(primary.get("cluster_id") or "")
    if not cid:
        raise HTTPException(status_code=400, detail="Record has no cluster_id")
    c_ids, c_metas = db.get_cluster_members(cid)
    updated = 0
    for doc_id, meta in zip(c_ids, c_metas):
        if not meta:
            continue
        new_meta = {**meta, "labels_json": labels_json}
        db.collection.update(ids=[doc_id], metadatas=[new_meta])
        updated += 1
    return {
        "ok": True,
        "mode": body.mode,
        "cluster_id": cid,
        "updated": updated,
        "chroma_path": chroma_path,
    }
