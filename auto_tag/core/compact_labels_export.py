"""紧凑标注导出：平行数组 + 字典压缩 labels/prefix/cluster。"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set

from auto_tag.core.config import settings
from auto_tag.core.duplicate_store import load_all_duplicate_rows
from auto_tag.core.path_prefix_registry import (
    REGISTRY_FILENAME,
    PathPrefixRegistry,
    resolve_stored_image_path,
)
from auto_tag.core.pipeline import normalize_work_dir, work_chroma_dir, work_log_dir
from auto_tag.core.vector_db import VectorDB

logger = logging.getLogger(__name__)


def _is_cluster_center(meta: Optional[Dict[str, Any]]) -> bool:
    if not meta:
        return False
    v = meta.get("is_cluster_center")
    return v is True or str(v).lower() in ("true", "1")


def _parse_labels_dict(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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


def _labels_nonempty(d: Dict[str, Any]) -> bool:
    return bool(d)


def _canon_labels(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _prefix_id_int(pid_str: str) -> int:
    try:
        return int(str(pid_str))
    except ValueError:
        return 0


def build_compact_export(work_dir: str) -> Dict[str, Any]:
    """
    返回完整结构（含平行数组与共享字典）。
    images 存相对路径段；与 prefix[prefix_id] 组合可得绝对路径。
    """
    wr = normalize_work_dir(work_dir)
    log_d = work_log_dir(wr)
    emb_d = work_chroma_dir(wr)
    reg = PathPrefixRegistry(log_d)

    if not emb_d or not os.path.isdir(emb_d):
        raise FileNotFoundError(f"向量索引目录不存在: {emb_d}")

    db = VectorDB(db_path=emb_d, collection_name=settings.collection_name)
    metas = db.iter_metadatas(batch_size=500)

    path_to_meta: Dict[str, Dict[str, Any]] = {}
    cluster_center_labels: Dict[str, Dict[str, Any]] = {}
    for m in metas:
        if not m:
            continue
        p = resolve_stored_image_path(m, reg)
        if not p:
            continue
        path_to_meta[p] = m
        if _is_cluster_center(m):
            cid = str(m.get("cluster_id") or "")
            if cid:
                cluster_center_labels[cid] = _parse_labels_dict(m)

    dup_file = os.path.join(log_d, settings.duplicate_links_filename)
    dup_rows = load_all_duplicate_rows(dup_file, log_dir=log_d)

    dup_to_anchor: Dict[str, str] = {}
    for row in dup_rows:
        dp = str(row.get("dup_path") or "").strip()
        ap = str(row.get("anchor_path") or "").strip()
        if dp and ap:
            dup_to_anchor[dp] = ap

    all_paths: Set[str] = set(path_to_meta.keys())
    for row in dup_rows:
        for k in ("anchor_path", "dup_path"):
            v = str(row.get(k) or "").strip()
            if v:
                all_paths.add(v)

    sorted_paths = sorted(all_paths)

    labels_out: Dict[int, Any] = {}
    labels_rev: Dict[str, int] = {}

    def alloc_label(obj: Dict[str, Any]) -> int:
        key = _canon_labels(obj)
        if key not in labels_rev:
            nid = len(labels_out)
            labels_out[nid] = obj
            labels_rev[key] = nid
        return labels_rev[key]

    cluster_out: Dict[int, str] = {}
    cluster_str_to_int: Dict[str, int] = {}

    def cluster_int(cs: str) -> int:
        if cs not in cluster_str_to_int:
            n = len(cluster_str_to_int)
            cluster_str_to_int[cs] = n
            cluster_out[n] = cs
        return cluster_str_to_int[cs]

    cluster_to_labels: Dict[int, int] = {}
    for cid_str, lab in cluster_center_labels.items():
        if _labels_nonempty(lab):
            cluster_to_labels[cluster_int(cid_str)] = alloc_label(lab)

    prefix_map: Dict[int, str] = {}
    for sid, pfx in reg.prefixes.items():
        prefix_map[_prefix_id_int(str(sid))] = str(pfx)

    images: List[str] = []
    labels_id: List[int] = []
    prefix_id: List[int] = []
    cluster_id: List[int] = []

    def effective_for_indexed_path(path: str, meta: Dict[str, Any]) -> Tuple[int, int, int]:
        own = _parse_labels_dict(meta)
        cid_str = str(meta.get("cluster_id") or "")
        ci = cluster_int(cid_str) if cid_str else cluster_int("__none__")

        if _labels_nonempty(own):
            return alloc_label(own), _prefix_id_int(reg.split(path)[0]), ci

        if path in dup_to_anchor:
            anch = dup_to_anchor[path]
            am = path_to_meta.get(anch)
            if am:
                al = _parse_labels_dict(am)
                if _labels_nonempty(al):
                    return alloc_label(al), _prefix_id_int(reg.split(path)[0]), ci
                ac = str(am.get("cluster_id") or "")
                if ac and ac in cluster_center_labels:
                    cl = cluster_center_labels[ac]
                    if _labels_nonempty(cl):
                        return alloc_label(cl), _prefix_id_int(reg.split(path)[0]), ci

        if cid_str and cid_str in cluster_center_labels:
            cl = cluster_center_labels[cid_str]
            if _labels_nonempty(cl):
                return alloc_label(cl), _prefix_id_int(reg.split(path)[0]), ci
        return alloc_label({}), _prefix_id_int(reg.split(path)[0]), ci

    for path in sorted_paths:
        pid_str, rel = reg.split(path)
        pi = _prefix_id_int(pid_str)
        meta = path_to_meta.get(path)

        if meta is not None:
            li, _, ci = effective_for_indexed_path(path, meta)
            images.append(rel)
            labels_id.append(li)
            prefix_id.append(pi)
            cluster_id.append(ci)
            continue

        if path in dup_to_anchor:
            anch = dup_to_anchor[path]
            am = path_to_meta.get(anch)
            ci = cluster_int("__dup_only__")
            if am:
                cid_str = str(am.get("cluster_id") or "")
                if cid_str:
                    ci = cluster_int(cid_str)
                al = _parse_labels_dict(am)
                if _labels_nonempty(al):
                    li = alloc_label(al)
                elif cid_str and cid_str in cluster_center_labels:
                    cl = cluster_center_labels[cid_str]
                    li = alloc_label(cl if _labels_nonempty(cl) else {})
                else:
                    li = alloc_label({})
            else:
                li = alloc_label({})
            p2_str, rel2 = reg.split(path)
            images.append(rel2)
            labels_id.append(li)
            prefix_id.append(_prefix_id_int(p2_str))
            cluster_id.append(ci)
            continue

        pid_str, rel = reg.split(path)
        images.append(rel)
        labels_id.append(alloc_label({}))
        prefix_id.append(_prefix_id_int(pid_str))
        cluster_id.append(cluster_int("__orphan__"))

    return {
        "images": images,
        "labels_id": labels_id,
        "prefix_id": prefix_id,
        "cluster_id": cluster_id,
        "labels": labels_out,
        "prefix": prefix_map,
        "cluster": cluster_out,
        "cluster_to_labels": cluster_to_labels,
        "export_meta": {
            "work_dir": wr,
            "chroma_path": emb_d,
            "log_dir": log_d,
            "row_count": len(images),
            "path_prefix_registry_file": os.path.join(log_d, REGISTRY_FILENAME),
        },
    }


def slice_compact_parallel(
    full: Dict[str, Any], *, offset: int, limit: int
) -> Dict[str, Any]:
    n = len(full.get("images") or [])
    off = max(0, offset)
    lim = max(0, limit)
    sl = slice(off, off + lim)
    em = dict(full.get("export_meta") or {})
    em.update(
        {
            "slice_offset": off,
            "slice_limit": lim,
            "slice_count": min(lim, max(0, n - off)),
            "total_rows": n,
        }
    )
    return {
        "export_meta": em,
        "images": (full.get("images") or [])[sl],
        "labels_id": (full.get("labels_id") or [])[sl],
        "prefix_id": (full.get("prefix_id") or [])[sl],
        "cluster_id": (full.get("cluster_id") or [])[sl],
    }


def slice_compact_chunk(full: Dict[str, Any], *, chunk_index: int, chunk_size: int) -> Dict[str, Any]:
    off = max(0, chunk_index) * max(1, chunk_size)
    return slice_compact_parallel(full, offset=off, limit=max(1, chunk_size))


def shared_compact_dict(full: Dict[str, Any]) -> Dict[str, Any]:
    em = dict(full.get("export_meta") or {})
    em["shared_export"] = True
    return {
        "export_meta": em,
        "labels": full.get("labels") or {},
        "prefix": full.get("prefix") or {},
        "cluster": full.get("cluster") or {},
        "cluster_to_labels": full.get("cluster_to_labels") or {},
    }
