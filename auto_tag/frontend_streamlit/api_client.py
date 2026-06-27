"""调用 FastAPI 后端的轻量客户端。"""
from typing import Any, Dict, List, Optional

import httpx


class AutoTagApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def health(self) -> Dict[str, Any]:
        r = httpx.get(f"{self.base_url}/api/health", timeout=30.0)
        r.raise_for_status()
        return r.json()

    def create_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = httpx.post(f"{self.base_url}/api/jobs", json=payload, timeout=60.0)
        r.raise_for_status()
        return r.json()

    def get_job(self, job_id: str) -> Dict[str, Any]:
        r = httpx.get(f"{self.base_url}/api/jobs/{job_id}", timeout=30.0)
        r.raise_for_status()
        return r.json()

    def get_job_logs(self, job_id: str, tail: int = 200) -> List[str]:
        r = httpx.get(
            f"{self.base_url}/api/jobs/{job_id}/logs",
            params={"tail": tail},
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json().get("lines", [])

    def list_records(
        self,
        offset: int = 0,
        limit: int = 50,
        cluster_id: Optional[str] = None,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"offset": offset, "limit": limit}
        if cluster_id:
            params["cluster_id"] = cluster_id
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        r = httpx.get(f"{self.base_url}/api/records", params=params, timeout=60.0)
        r.raise_for_status()
        return r.json()

    def list_duplicates(
        self,
        *,
        work_dir: Optional[str] = None,
        log_dir: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"offset": offset, "limit": limit}
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        elif log_dir is not None:
            params["log_dir"] = log_dir
        else:
            raise ValueError("Provide work_dir or log_dir")
        r = httpx.get(
            f"{self.base_url}/api/duplicates",
            params=params,
            timeout=60.0,
        )
        r.raise_for_status()
        return r.json()

    def record_by_path(
        self, image_path: str, work_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"image_path": image_path}
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        r = httpx.get(
            f"{self.base_url}/api/records/by_path",
            params=params,
            timeout=60.0,
        )
        r.raise_for_status()
        return r.json()

    def fetch_preview_png(
        self,
        image_path: str,
        work_dir: Optional[str] = None,
        *,
        image_width: int = 0,
        image_height: int = 0,
        yuv_type: str = "nv21",
        b_yuv_image: bool = False,
        mixed_yuv: bool = False,
        rotate_angle: Optional[str] = None,
    ) -> bytes:
        params: Dict[str, Any] = {
            "image_path": image_path,
            "image_width": image_width,
            "image_height": image_height,
            "yuv_type": yuv_type,
            "b_yuv_image": b_yuv_image,
            "mixed_yuv": mixed_yuv,
        }
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        if rotate_angle:
            params["rotate_angle"] = rotate_angle
        r = httpx.get(
            f"{self.base_url}/api/records/preview",
            params=params,
            timeout=120.0,
        )
        r.raise_for_status()
        return r.content

    def update_record_labels(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/api/records/update_labels",
            json=payload,
            timeout=60.0,
        )
        r.raise_for_status()
        return r.json()

    def database_stats(
        self,
        work_dir: Optional[str] = None,
        *,
        config_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        if config_path is not None and str(config_path).strip():
            params["config_path"] = str(config_path).strip()
        r = httpx.get(
            f"{self.base_url}/api/database/stats",
            params=params,
            timeout=120.0,
        )
        r.raise_for_status()
        return r.json()

    def export_embeddings(
        self,
        *,
        work_dir: Optional[str] = None,
        mode: str = "range",
        offset: int = 0,
        limit: int = 200_000,
        cluster_id: Optional[str] = None,
        chunk_index: int = 0,
        chunk_size: int = 200_000,
    ) -> bytes:
        params: Dict[str, Any] = {
            "mode": mode,
            "offset": offset,
            "limit": limit,
            "chunk_index": chunk_index,
            "chunk_size": chunk_size,
        }
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        if cluster_id is not None and str(cluster_id).strip():
            params["cluster_id"] = str(cluster_id).strip()
        r = httpx.get(
            f"{self.base_url}/api/database/export_embeddings",
            params=params,
            timeout=600.0,
        )
        r.raise_for_status()
        return r.content

    def export_duplicates(
        self,
        *,
        work_dir: Optional[str] = None,
        mode: str = "range",
        offset: int = 0,
        limit: int = 200_000,
        chunk_index: int = 0,
        chunk_size: int = 200_000,
    ) -> bytes:
        params: Dict[str, Any] = {
            "mode": mode,
            "offset": offset,
            "limit": limit,
            "chunk_index": chunk_index,
            "chunk_size": chunk_size,
        }
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        r = httpx.get(
            f"{self.base_url}/api/database/export_duplicates",
            params=params,
            timeout=600.0,
        )
        r.raise_for_status()
        return r.content

    def database_recompute_relations(
        self, work_dir: Optional[str] = None, *, timeout: float = 86400.0
    ) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/api/database/recompute_relations",
            json={"work_dir": work_dir},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def database_rebuild_relations(
        self, work_dir: Optional[str] = None, *, timeout: float = 86400.0
    ) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/api/database/rebuild_relations",
            json={"work_dir": work_dir},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def database_reannotate(
        self, payload: Dict[str, Any], *, timeout: float = 86400.0
    ) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/api/database/reannotate",
            json=payload,
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def export_compact_shared(self, work_dir: Optional[str] = None) -> bytes:
        params: Dict[str, Any] = {}
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        r = httpx.get(
            f"{self.base_url}/api/database/export_compact_shared",
            params=params,
            timeout=86400.0,
        )
        r.raise_for_status()
        return r.content

    def export_compact_slice(
        self,
        *,
        work_dir: Optional[str] = None,
        offset: int = 0,
        limit: int = 200_000,
    ) -> bytes:
        params: Dict[str, Any] = {"offset": offset, "limit": limit}
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        r = httpx.get(
            f"{self.base_url}/api/database/export_compact_slice",
            params=params,
            timeout=86400.0,
        )
        r.raise_for_status()
        return r.content

    def export_compact_chunk(
        self,
        *,
        work_dir: Optional[str] = None,
        chunk_index: int = 0,
        chunk_size: int = 200_000,
    ) -> bytes:
        params: Dict[str, Any] = {
            "chunk_index": chunk_index,
            "chunk_size": chunk_size,
        }
        if work_dir is not None and str(work_dir).strip():
            params["work_dir"] = str(work_dir).strip()
        r = httpx.get(
            f"{self.base_url}/api/database/export_compact_chunk",
            params=params,
            timeout=86400.0,
        )
        r.raise_for_status()
        return r.content
