"""数据库维护：按快照重建关系、批量重标。"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from auto_tag.core.config import settings
from auto_tag.core.db_build_snapshot import read_build_snapshot, write_build_snapshot
from auto_tag.core.duplicate_store import DuplicateLinkWriter, load_all_duplicate_rows
from auto_tag.core.path_prefix_registry import PathPrefixRegistry, resolve_stored_image_path
from auto_tag.core.pipeline import (
    PipelineConfig,
    normalize_work_dir,
    run_annotation_pipeline,
    work_chroma_dir,
    work_log_dir,
)
from auto_tag.core.utils.load_image import load_image_for_job
from auto_tag.core.vector_db import VectorDB
from auto_tag.core.vlm_client import VLMClient

logger = logging.getLogger(__name__)


def _parse_labels_from_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    raw = meta.get("labels_json")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return {}


def _is_cluster_center(meta: Dict[str, Any]) -> bool:
    v = meta.get("is_cluster_center")
    return v is True or str(v).lower() in ("true", "1")


def _labels_json_str_from_meta(meta: Dict[str, Any]) -> str:
    raw = meta.get("labels_json")
    if raw is None:
        return "{}"
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    s = str(raw).strip()
    return s if s else "{}"


def _decode_meta_from_stored(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "media_kind": str(meta.get("media_kind", "rgb")),
        "pix_w": int(meta.get("pix_w", 0)),
        "pix_h": int(meta.get("pix_h", 0)),
        "yuv_layout": str(meta.get("yuv_layout", "")),
    }


def _row_meta_for_recompute(
    path: str,
    cluster_id: str,
    is_center: bool,
    labels_json: str,
    decode_meta: Dict[str, Any],
    reg: PathPrefixRegistry,
) -> Dict[str, Any]:
    """与 ImageAutoAnnotator._row_meta 一致，避免导入 Annotator 拉取 CLIP/VLM。"""
    out: Dict[str, Any] = {
        "cluster_id": cluster_id,
        "is_cluster_center": bool(is_center),
        "labels_json": labels_json,
        "media_kind": str(decode_meta.get("media_kind", "rgb")),
        "pix_w": int(decode_meta.get("pix_w", 0)),
        "pix_h": int(decode_meta.get("pix_h", 0)),
        "yuv_layout": str(decode_meta.get("yuv_layout", "")),
    }
    pid, rel = reg.split(path)
    out["path_prefix_id"] = str(pid)
    out["image_rel_path"] = rel
    return out


def _generate_unique_cluster_id(db: VectorDB) -> str:
    while True:
        new_id = f"cls_{uuid.uuid4().hex[:8]}"
        existing = db.collection.get(where={"cluster_id": new_id}, limit=1)
        if not existing.get("ids"):
            return new_id
        logger.warning("cluster_id collision %s, retry", new_id)


def _norm_dup_path_key(p: str) -> str:
    """侧车去重 / 查表用的路径规范化键。"""
    s = (p or "").strip()
    if not s:
        return ""
    p = os.path.normpath(os.path.expanduser(s))
    try:
        if os.path.exists(p):
            return os.path.realpath(p)
    except OSError:
        pass
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(s)))
    except OSError:
        return p


def _dup_pair_key(anchor_path: str, dup_path: str) -> Tuple[str, str]:
    return _norm_dup_path_key(anchor_path), _norm_dup_path_key(dup_path)


def _path_variants_for_dup_lookup(p: str) -> List[str]:
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


def _build_anchor_path_to_doc_id(
    db: VectorDB, reg: PathPrefixRegistry
) -> Dict[str, str]:
    """重算写入完成后：规范化路径 -> Chroma 文档 id（每键保留首条）。"""
    out: Dict[str, str] = {}
    try:
        rows = db.get_all_documents(batch_size=500)
    except ValueError:
        return out
    for doc_id, _emb, meta in rows:
        if not meta:
            continue
        path = resolve_stored_image_path(meta, reg)
        if not str(path).strip():
            path = str(meta.get("image_path") or "").strip()
        if not path:
            continue
        k = _norm_dup_path_key(path)
        if k and k not in out:
            out[k] = str(doc_id)
    return out


def _merge_legacy_duplicate_links_after_recompute(
    *,
    dup_writer: DuplicateLinkWriter,
    db: VectorDB,
    reg: PathPrefixRegistry,
    dup_store_path: str,
    log_dir: str,
    preserved: List[Tuple[str, str, float]],
) -> int:
    """将删库前保留的侧车行合并回新文件：按路径去重、刷新 anchor_id、丢弃超过当前 tau_dup 的距离。"""
    if not preserved:
        return 0
    tau_dup_limit = float(settings.tau_dup)
    anchor_to_id = _build_anchor_path_to_doc_id(db, reg)
    existing_pairs: set[Tuple[str, str]] = set()
    try:
        fresh = load_all_duplicate_rows(dup_store_path, log_dir=log_dir)
        for r in fresh:
            pk = _dup_pair_key(
                str(r.get("anchor_path") or ""),
                str(r.get("dup_path") or ""),
            )
            if pk[0] and pk[1]:
                existing_pairs.add(pk)
    except Exception:
        logger.exception("读取重算后侧车用于去重失败")
    merged = 0
    for ap, dp, dist in preserved:
        if float(dist) > tau_dup_limit + 1e-12:
            continue
        pk = _dup_pair_key(ap, dp)
        if not pk[0] or not pk[1]:
            continue
        if pk in existing_pairs:
            continue
        nid = anchor_to_id.get(pk[0])
        if not nid:
            for v in _path_variants_for_dup_lookup(ap):
                nk = _norm_dup_path_key(v)
                nid = anchor_to_id.get(nk)
                if nid:
                    break
        if not nid:
            logger.debug(
                "合并侧车跳过：重算后索引中无锚点路径 %s",
                ap,
            )
            continue
        try:
            dup_writer.append(str(nid), ap, dp, float(dist))
        except Exception:
            logger.exception("合并写入侧车失败: %s -> %s", ap, dp)
            continue
        existing_pairs.add(pk)
        merged += 1
    return merged


def recompute_relations_only(work_dir: str) -> Dict[str, Any]:
    """
    在**不调用 CLIP/VLM** 的前提下，按当前 tau_dup/tau_cls 用已有向量与元数据中的 labels
    重新执行双阈值逻辑（固定按路径字典序处理顺序），并重建向量关系与侧车。

    侧车：先按路径备份旧 duplicate_links（距离须仍满足当前 tau_dup），重算产生的新 Stage1
    行写入后，再把备份中**与重算结果去重后**且锚点仍在索引中的行合并回去，避免未入库近重复
    仅因「仅重算」而整表丢失；Chroma 文档 id 在合并时按锚点路径重新解析。
    """
    wr = normalize_work_dir(work_dir)
    log_d = work_log_dir(wr)
    emb_d = work_chroma_dir(wr)
    snap = read_build_snapshot(log_d)
    if not snap:
        raise ValueError("未找到 auto_tag_db_build_snapshot.json，无法对齐任务参数")

    if not os.path.isdir(emb_d):
        raise FileNotFoundError(f"向量索引目录不存在: {emb_d}")

    reg = PathPrefixRegistry(log_d)
    db = VectorDB(db_path=emb_d, collection_name=settings.collection_name)
    tau_dup = float(settings.tau_dup)
    tau_cls = float(settings.tau_cls)

    try:
        raw_rows = db.get_all_documents(batch_size=500)
    except ValueError as e:
        raise ValueError(str(e)) from e

    enriched: List[Dict[str, Any]] = []
    for _old_id, emb, meta in raw_rows:
        path = resolve_stored_image_path(meta, reg)
        if not str(path).strip():
            ip = meta.get("image_path")
            path = str(ip).strip() if ip else ""
        if not path:
            logger.warning("跳过无路径记录（old_id 已忽略）")
            continue
        enriched.append({"embedding": emb, "metadata": meta, "path": path})

    if not enriched:
        raise ValueError("没有可重算的有效索引记录（均需能解析出路径）")

    enriched.sort(key=lambda x: x["path"])

    dup_path = os.path.join(log_d, settings.duplicate_links_filename)
    preserved_duplicate_rows: List[Tuple[str, str, float]] = []
    if settings.record_stage1_duplicates and os.path.isfile(dup_path):
        try:
            legacy = load_all_duplicate_rows(dup_path, log_dir=log_d)
            for r in legacy:
                ap = str(r.get("anchor_path") or "").strip()
                dp = str(r.get("dup_path") or "").strip()
                if not ap or not dp:
                    continue
                try:
                    dist = float(r.get("distance", 0))
                except (TypeError, ValueError):
                    continue
                if dist > tau_dup + 1e-12:
                    continue
                preserved_duplicate_rows.append((ap, dp, dist))
        except Exception:
            logger.exception("重算前读取侧车备份失败")

    if os.path.isfile(dup_path):
        try:
            os.remove(dup_path)
        except OSError as e:
            logger.warning("remove duplicate store: %s", e)

    dup_writer: Optional[DuplicateLinkWriter] = None
    if settings.record_stage1_duplicates:
        dup_writer = DuplicateLinkWriter(
            log_d, reg, filename=settings.duplicate_links_filename
        )

    db.recreate_empty_collection()

    stats = {
        "stage1_skips": 0,
        "stage2_joins": 0,
        "indexed": 0,
        "skipped_no_neighbor": 0,
    }
    ids_to_add: List[str] = []
    embs_to_add: List[List[float]] = []
    metas_to_add: List[Dict[str, Any]] = []
    add_chunk = 200

    def flush_batch() -> None:
        nonlocal ids_to_add, embs_to_add, metas_to_add
        if ids_to_add:
            db.add_batch(ids_to_add, embs_to_add, metas_to_add)
            stats["indexed"] += len(ids_to_add)
            ids_to_add, embs_to_add, metas_to_add = [], [], []

    first = enriched[0]
    cid0 = _generate_unique_cluster_id(db)
    db.add_batch(
        [str(uuid.uuid4())],
        [first["embedding"]],
        [
            _row_meta_for_recompute(
                first["path"],
                cid0,
                True,
                _labels_json_str_from_meta(first["metadata"]),
                _decode_meta_from_stored(first["metadata"]),
                reg,
            )
        ],
    )
    stats["indexed"] += 1

    for rec in enriched[1:]:
        emb = rec["embedding"]
        path = rec["path"]
        meta = rec["metadata"]
        decode = _decode_meta_from_stored(meta)

        dists, metas_nn, nids = db.query_batch([emb], n_results=1)
        if not dists or not dists[0]:
            flush_batch()
            cid_new = _generate_unique_cluster_id(db)
            db.add_batch(
                [str(uuid.uuid4())],
                [emb],
                [
                    _row_meta_for_recompute(
                        path,
                        cid_new,
                        True,
                        _labels_json_str_from_meta(meta),
                        decode,
                        reg,
                    )
                ],
            )
            stats["indexed"] += 1
            stats["skipped_no_neighbor"] += 1
            continue

        d0 = float(dists[0][0])
        nm = metas_nn[0][0] if metas_nn and metas_nn[0] else {}
        nid = nids[0][0] if nids and nids[0] else ""

        if d0 <= tau_dup:
            stats["stage1_skips"] += 1
            if dup_writer and nid:
                ap = resolve_stored_image_path(nm, reg)
                if not ap:
                    ap = str(nm.get("image_path") or "")
                dup_writer.append(str(nid), ap, path, d0)
            continue

        if d0 <= tau_cls:
            stats["stage2_joins"] += 1
            cluster_id = str(nm.get("cluster_id") or "").strip()
            if not cluster_id:
                cluster_id = _generate_unique_cluster_id(db)
            labels_inh = _labels_json_str_from_meta(nm)
            ids_to_add.append(str(uuid.uuid4()))
            embs_to_add.append(emb)
            metas_to_add.append(
                _row_meta_for_recompute(
                    path,
                    cluster_id,
                    False,
                    labels_inh,
                    decode,
                    reg,
                )
            )
            if len(ids_to_add) >= add_chunk:
                flush_batch()
            continue

        flush_batch()
        cid_new = _generate_unique_cluster_id(db)
        db.add_batch(
            [str(uuid.uuid4())],
            [emb],
            [
                _row_meta_for_recompute(
                    path,
                    cid_new,
                    True,
                    _labels_json_str_from_meta(meta),
                    decode,
                    reg,
                )
            ],
        )
        stats["indexed"] += 1

    flush_batch()

    merged_legacy_duplicate_links = 0
    if (
        settings.record_stage1_duplicates
        and dup_writer
        and preserved_duplicate_rows
    ):
        merged_legacy_duplicate_links = _merge_legacy_duplicate_links_after_recompute(
            dup_writer=dup_writer,
            db=db,
            reg=reg,
            dup_store_path=dup_path,
            log_dir=log_d,
            preserved=preserved_duplicate_rows,
        )

    cfg = PipelineConfig(
        work_dir=wr,
        input_dirs=[str(x) for x in (snap.get("input_dirs") or []) if str(x).strip()],
        image_ls_files=[str(x) for x in (snap.get("image_ls_files") or []) if str(x).strip()],
        rotate_angle=snap.get("rotate_angle"),
        b_yuv_image=bool(snap.get("b_yuv_image", False)),
        mixed_yuv=bool(snap.get("mixed_yuv", False)),
        yuv_type=str(snap.get("yuv_type") or "nv21"),
        image_width=int(snap.get("image_width") or 0),
        image_height=int(snap.get("image_height") or 0),
        skip_if_in_db=False,
        record_stage1_duplicates=settings.record_stage1_duplicates,
    )
    try:
        write_build_snapshot(log_d, cfg)
    except Exception:
        logger.exception("write_build_snapshot after recompute failed")

    return {
        "ok": True,
        "mode": "recompute_relations_only",
        "message": (
            "已按当前 τ_dup/τ_cls 重算簇与侧车：复用原向量与元数据中的 labels，未调用 VLM/CLIP。"
            " 处理顺序为路径字典序，与在线流水线顺序可能不同。"
            " 删库前侧车已按路径备份，重算写入后再合并仍满足当前 τ_dup 且锚点仍在索引中的行，"
            "并刷新 anchor_id。"
        ),
        "vlm_calls": 0,
        "source_records": len(enriched),
        "indexed_count": stats["indexed"],
        "stage1_skips": stats["stage1_skips"],
        "stage2_joins": stats["stage2_joins"],
        "skipped_no_neighbor": stats["skipped_no_neighbor"],
        "preserved_duplicate_links_loaded": len(preserved_duplicate_rows),
        "merged_legacy_duplicate_links": merged_legacy_duplicate_links,
        "embedding_store_path": emb_d,
        "log_dir": log_d,
    }


def rebuild_relations(work_dir: str) -> Dict[str, Any]:
    """清空向量集合与侧车，按快照中的 input 列表用当前 settings 重跑流水线（完全重建）。"""
    wr = normalize_work_dir(work_dir)
    log_d = work_log_dir(wr)
    emb_d = work_chroma_dir(wr)
    snap = read_build_snapshot(log_d)
    if not snap:
        raise ValueError("未找到 auto_tag_db_build_snapshot.json")
    input_dirs = [str(x) for x in (snap.get("input_dirs") or []) if str(x).strip()]
    if not input_dirs:
        raise ValueError("快照中缺少 input_dirs，请先成功跑完一次任务以写入新快照")

    cfg = PipelineConfig(
        work_dir=wr,
        input_dirs=input_dirs,
        image_ls_files=[str(x) for x in (snap.get("image_ls_files") or []) if str(x).strip()],
        rotate_angle=snap.get("rotate_angle"),
        b_yuv_image=bool(snap.get("b_yuv_image", False)),
        mixed_yuv=bool(snap.get("mixed_yuv", False)),
        yuv_type=str(snap.get("yuv_type") or "nv21"),
        image_width=int(snap.get("image_width") or 0),
        image_height=int(snap.get("image_height") or 0),
        skip_if_in_db=False,
        record_stage1_duplicates=settings.record_stage1_duplicates,
    )

    dup_path = os.path.join(log_d, settings.duplicate_links_filename)
    if os.path.isfile(dup_path):
        try:
            os.remove(dup_path)
        except OSError as e:
            logger.warning("remove duplicate store: %s", e)

    if not os.path.isdir(emb_d):
        os.makedirs(emb_d, exist_ok=True)
    db = VectorDB(db_path=emb_d, collection_name=settings.collection_name)
    db.recreate_empty_collection()

    result = run_annotation_pipeline(cfg)
    try:
        write_build_snapshot(log_d, cfg)
    except Exception:
        logger.exception("write_build_snapshot after rebuild failed")

    return {
        "ok": True,
        "mode": "full_rebuild_index",
        "message": "已完全重建索引：按快照 input 重跑流水线（CLIP+VLM 等，与排队任务互斥）。",
        "total_images": result.total_images,
        "processed_ok": result.processed_ok,
        "failed_count": len(result.failed_paths),
        "embedding_store_path": emb_d,
        "log_dir": log_d,
    }


def reannotate(
    work_dir: str,
    *,
    full_refresh: bool,
    incremental: bool,
    centers_only: bool,
) -> Dict[str, Any]:
    """遍历索引中文档，按需调用 VLM 更新 labels_json。"""
    if full_refresh and incremental:
        raise ValueError("full_refresh 与 incremental 互斥")
    if not full_refresh and not incremental:
        raise ValueError("请选择 full_refresh 或 incremental 之一")

    wr = normalize_work_dir(work_dir)
    log_d = work_log_dir(wr)
    emb_d = work_chroma_dir(wr)
    if not os.path.isdir(emb_d):
        raise FileNotFoundError(f"向量索引目录不存在: {emb_d}")

    reg = PathPrefixRegistry(log_d)
    db = VectorDB(db_path=emb_d, collection_name=settings.collection_name)
    vlm = VLMClient(
        model_name=settings.vlm_model_name,
        api_key=settings.vlm_api_key,
    )

    n = db.count()
    offset = 0
    batch = 40
    updated = 0
    vlm_calls = 0
    skipped = 0
    errs: List[str] = []

    while offset < n:
        r = db.collection.get(
            limit=min(batch, n - offset),
            offset=offset,
            include=["metadatas"],
        )
        ids = r.get("ids") or []
        metas = r.get("metadatas") or []
        if not ids:
            break
        offset += len(ids)

        for doc_id, meta in zip(ids, metas):
            if not meta:
                skipped += 1
                continue
            if centers_only and not _is_cluster_center(meta):
                skipped += 1
                continue
            path = resolve_stored_image_path(meta, reg)
            if not path or not os.path.isfile(path):
                skipped += 1
                continue
            pc = PipelineConfig(
                b_yuv_image=str(meta.get("media_kind") or "") == "yuv",
                mixed_yuv=False,
                yuv_type=str(meta.get("yuv_layout") or "nv21"),
                image_width=int(meta.get("pix_w") or 0),
                image_height=int(meta.get("pix_h") or 0),
            )
            try:
                img = load_image_for_job(
                    path,
                    b_yuv_image=pc.b_yuv_image,
                    mixed_yuv=pc.mixed_yuv,
                    yuv_type=pc.yuv_type,
                    image_height=pc.image_height,
                    image_width=pc.image_width,
                    rotate_angle=None,
                )
            except Exception as e:
                errs.append(f"{path}: load {e}")
                continue

            existing = _parse_labels_from_meta(meta)
            try:
                if full_refresh:
                    new_labels = vlm.annotate_image(img)
                else:
                    new_labels = vlm.annotate_image_incremental(img, existing)
                vlm_calls += 1
            except Exception as e:
                errs.append(f"{path}: vlm {e}")
                continue

            new_meta = {
                **meta,
                "labels_json": json.dumps(new_labels, ensure_ascii=False),
            }
            try:
                db.update_document_metadata(doc_id, new_meta)
                updated += 1
            except Exception as e:
                errs.append(f"{path}: update {e}")

    return {
        "ok": True,
        "message": "标注更新完成。",
        "documents_total": n,
        "updated": updated,
        "vlm_calls": vlm_calls,
        "skipped": skipped,
        "errors_sample": errs[:20],
        "error_count": len(errs),
        "embedding_store_path": emb_d,
    }
