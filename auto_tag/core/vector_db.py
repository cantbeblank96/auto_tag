import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import chromadb

from auto_tag.core.path_prefix_registry import PathPrefixRegistry

logger = logging.getLogger(__name__)


def _resolve_db_path(db_path: str) -> str:
    """转为绝对路径并确保目录存在（避免后台线程里相对路径落到错误 cwd 导致 SQLite code 14）。"""
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(db_path.strip())))
    os.makedirs(resolved, exist_ok=True)
    return resolved


class VectorDB:
    def __init__(self, db_path: str, collection_name: str):
        resolved = _resolve_db_path(db_path)
        logger.info("Initializing ChromaDB at %s...", resolved)
        try:
            self.client = chromadb.PersistentClient(path=resolved)
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "ChromaDB collection '%s' ready. Current doc count: %s",
                collection_name,
                self.collection.count(),
            )
        except Exception as e:
            logger.error("Failed to initialize ChromaDB: %s", e)
            raise

    def count(self) -> int:
        return self.collection.count()

    def recreate_empty_collection(self) -> None:
        """删除并重建空集合（用于重建关系）。"""
        name = self.collection.name
        try:
            self.client.delete_collection(name)
        except Exception as e:
            logger.warning("delete_collection: %s", e)
        self.collection = self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def query_batch(
        self,
        query_embeddings: List[List[float]],
        n_results: int = 1,
    ) -> Tuple[List[List[float]], List[List[Dict[str, Any]]], List[List[str]]]:
        if self.count() == 0:
            return [], [], []
        try:
            results = self.collection.query(
                query_embeddings=query_embeddings,
                n_results=n_results,
                include=["distances", "metadatas"],
            )
            return (
                results.get("distances", []),
                results.get("metadatas", []),
                results.get("ids", []),
            )
        except Exception as e:
            logger.error("Error during ChromaDB batch query: %s", e)
            raise

    def add_batch(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
    ) -> None:
        if not ids:
            return
        try:
            self.collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas)
            logger.debug("Successfully added %d records to ChromaDB.", len(ids))
        except Exception as e:
            logger.error("Failed to add batch to ChromaDB: %s", e)
            raise

    def has_image_path(
        self,
        image_path: str,
        registry: Optional[PathPrefixRegistry] = None,
    ) -> bool:
        full = os.path.realpath(os.path.abspath(os.path.expanduser(str(image_path).strip())))
        if self.count() == 0:
            return False
        try:
            if registry is not None:
                pid, rel = registry.split(full)
                r = self.collection.get(
                    where={"path_prefix_id": str(pid), "image_rel_path": rel},
                    limit=1,
                )
                if r.get("ids"):
                    return True
            r2 = self.collection.get(where={"image_path": full}, limit=1)
            return bool(r2.get("ids"))
        except Exception as e:
            logger.error("has_image_path failed: %s", e)
            return False

    def delete_by_image_path(
        self,
        image_path: str,
        registry: Optional[PathPrefixRegistry] = None,
    ) -> int:
        if self.count() == 0:
            return 0
        full = os.path.realpath(os.path.abspath(os.path.expanduser(str(image_path).strip())))
        try:
            all_ids: List[str] = []
            if registry is not None:
                pid, rel = registry.split(full)
                r = self.collection.get(
                    where={"path_prefix_id": str(pid), "image_rel_path": rel},
                    limit=100000,
                )
                all_ids.extend(r.get("ids") or [])
            r2 = self.collection.get(where={"image_path": full}, limit=100000)
            all_ids.extend(r2.get("ids") or [])
            seen = set()
            uniq = [x for x in all_ids if x not in seen and not seen.add(x)]
            if uniq:
                self.collection.delete(ids=uniq)
            return len(uniq)
        except Exception as e:
            logger.error("delete_by_image_path failed: %s", e)
            raise

    def get_by_image_path(
        self,
        image_path: str,
        *,
        limit: int = 50,
        registry: Optional[PathPrefixRegistry] = None,
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        if self.count() == 0:
            return [], []
        full = os.path.realpath(os.path.abspath(os.path.expanduser(str(image_path).strip())))
        if registry is not None:
            pid, rel = registry.split(full)
            r = self.collection.get(
                where={"path_prefix_id": str(pid), "image_rel_path": rel},
                limit=limit,
                include=["metadatas"],
            )
            if r.get("ids"):
                return list(r.get("ids") or []), list(r.get("metadatas") or [])
        r2 = self.collection.get(
            where={"image_path": full},
            limit=limit,
            include=["metadatas"],
        )
        return list(r2.get("ids") or []), list(r2.get("metadatas") or [])

    def get_cluster_members(
        self, cluster_id: str, *, limit: int = 100000
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        r = self.collection.get(
            where={"cluster_id": cluster_id},
            limit=limit,
            include=["metadatas"],
        )
        return list(r.get("ids") or []), list(r.get("metadatas") or [])

    def get_all_documents(
        self, *, batch_size: int = 500
    ) -> List[Tuple[str, List[float], Dict[str, Any]]]:
        """返回 (doc_id, embedding, metadata) 全量列表，供仅重算关系等复用向量。"""
        n = self.count()
        out: List[Tuple[str, List[float], Dict[str, Any]]] = []
        offset = 0
        while offset < n:
            r = self.collection.get(
                limit=min(batch_size, n - offset),
                offset=offset,
                include=["embeddings", "metadatas"],
            )
            ids = r.get("ids") or []
            embs = r.get("embeddings")
            metas = r.get("metadatas") or []
            if embs is None:
                raise ValueError(
                    "Chroma 未返回 embeddings（集合可能建于旧版本），无法仅重算关系"
                )
            for i, doc_id in enumerate(ids):
                emb = embs[i] if i < len(embs) else None
                meta = metas[i] if i < len(metas) else None
                if emb is None or not meta:
                    continue
                out.append((str(doc_id), list(emb), meta))
            offset += len(ids)
            if not ids:
                break
        return out

    def iter_metadatas(
        self, *, batch_size: int = 500
    ) -> List[Dict[str, Any]]:
        """返回全部 metadata（分批拉取后合并）。"""
        n = self.count()
        out: List[Dict[str, Any]] = []
        offset = 0
        while offset < n:
            r = self.collection.get(
                limit=min(batch_size, n - offset),
                offset=offset,
                include=["metadatas"],
            )
            ids = r.get("ids") or []
            metas = r.get("metadatas") or []
            for m in metas:
                if m:
                    out.append(m)
            offset += len(ids)
            if not ids:
                break
        return out

    def update_document_metadata(self, doc_id: str, metadata: Dict[str, Any]) -> None:
        self.collection.update(ids=[doc_id], metadatas=[metadata])


if __name__ == "__main__":
    from auto_tag.core.config import settings

    logging.basicConfig(level=logging.INFO)
    try:
        db = VectorDB(db_path=settings.db_path, collection_name=settings.collection_name)
        print(f"Vector DB test successful. Current document count: {db.count()}")
    except Exception as e:
        print(f"Vector DB test failed: {e}")
