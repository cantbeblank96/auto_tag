"""紧凑导出：按完整路径去重，保持平行数组与切片一致。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auto_tag.core import compact_labels_export as compact
from auto_tag.core.path_prefix_registry import PathPrefixRegistry


class TestCompactExportPaths(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work_dir = Path(temp.name)
        self.log_dir = self.work_dir / "log"
        self.registry = PathPrefixRegistry(str(self.log_dir))
        self.images_dir = self.work_dir / "images"
        self.images_dir.mkdir()
        (self.images_dir / "z").mkdir()
        self.prefix_id = int(self.registry.register_abs_dir(str(self.images_dir)))
        (self.work_dir / "embedding_index").mkdir()

    def build(self, metadatas: list[dict], rows: list[dict]) -> dict:
        sidecar = self.log_dir / "duplicate_links.jsonl"
        sidecar.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        # Keep registry and sidecar reads real; replace only the vector database.
        with mock.patch.object(compact.settings, "embedding_subdir", "embedding_index"), \
             mock.patch.object(compact.settings, "duplicate_links_filename", sidecar.name), \
             mock.patch.object(compact, "VectorDB") as db:
            db.return_value.iter_metadatas.return_value = metadatas
            return compact.build_compact_export(str(self.work_dir))

    @staticmethod
    def metadata(path: Path, scene: str = "indoor") -> dict:
        return {
            "image_path": str(path),
            "labels_json": json.dumps({"scene": scene}),
            "cluster_id": scene,
            "is_cluster_center": True,
            "annotation_status": "done",
        }

    def test_first_fifteen_path_aliases_are_not_appended_to_export(self) -> None:
        paths = [self.images_dir / f"{i:02}.jpg" for i in range(30)]
        rows = [
            {
                "anchor_path": str(paths[29]),
                "dup_path": str(self.images_dir / "z" / ".." / f"{i:02}.jpg"),
            }
            for i in range(15)
        ]
        full = self.build([self.metadata(p) for p in paths], rows)

        self.assertEqual(full["images"], [f"{i:02}.jpg" for i in range(30)])
        self.assertEqual(full["labels_id"], [0] * 30)
        self.assertEqual(full["prefix_id"], [self.prefix_id] * 30)
        self.assertEqual(full["cluster_id"], [0] * 30)
        self.assertEqual(full["export_meta"]["row_count"], 30)

        tail = compact.slice_compact_parallel(full, offset=25, limit=20)
        self.assertEqual(tail["images"], ["25.jpg", "26.jpg", "27.jpg", "28.jpg", "29.jpg"])
        self.assertEqual(tail["export_meta"]["slice_count"], 5)
        self.assertEqual(tail["export_meta"]["total_rows"], 30)
        for key in ("labels_id", "prefix_id", "cluster_id"):
            self.assertEqual(len(tail[key]), 5)
        chunk = compact.slice_compact_chunk(full, chunk_index=1, chunk_size=25)
        for key in ("images", "labels_id", "prefix_id", "cluster_id"):
            self.assertEqual(chunk[key], tail[key])

    def test_anchor_alias_does_not_add_an_extra_image(self) -> None:
        anchor = self.images_dir / "a.jpg"
        full = self.build([self.metadata(anchor)], [{
            "anchor_path": str(self.images_dir / "z" / ".." / "a.jpg"),
            "dup_path": str(self.images_dir / "b.jpg"),
        }])

        self.assertEqual(full["images"], ["a.jpg", "b.jpg"])
        self.assertEqual(full["labels_id"], [0, 0])
        self.assertEqual(full["cluster_id"], [0, 0])

    def test_sidecar_only_path_aliases_export_once(self) -> None:
        anchor = self.images_dir / "a.jpg"
        full = self.build([self.metadata(anchor)], [
            {"anchor_path": str(anchor), "dup_path": str(self.images_dir / "b.jpg")},
            {"anchor_path": str(anchor), "dup_path": str(self.images_dir / "z" / ".." / "b.jpg")},
        ])

        self.assertEqual(full["images"], ["a.jpg", "b.jpg"])
        self.assertEqual(full["labels_id"], [0, 0])
        self.assertEqual(full["prefix_id"], [self.prefix_id, self.prefix_id])
        self.assertEqual(full["cluster_id"], [0, 0])

    def test_same_relative_name_in_different_roots_is_preserved(self) -> None:
        other_dir = self.work_dir / "other"
        other_dir.mkdir()
        other_prefix = int(self.registry.register_abs_dir(str(other_dir)))
        full = self.build([
            self.metadata(self.images_dir / "a.jpg", "indoor"),
            self.metadata(other_dir / "a.jpg", "outdoor"),
        ], [])

        self.assertEqual(full["images"], ["a.jpg", "a.jpg"])
        self.assertEqual(full["prefix_id"], [self.prefix_id, other_prefix])
        self.assertEqual(
            [full["labels"][label_id] for label_id in full["labels_id"]],
            [{"scene": "indoor"}, {"scene": "outdoor"}],
        )

    def test_empty_sidecar_path_is_not_resolved_to_working_directory(self) -> None:
        full = self.build([], [{"anchor_path": " ", "dup_path": ""}])
        self.assertEqual(full["images"], [])

    def test_relative_sidecar_path_matches_absolute_index_path(self) -> None:
        path = self.images_dir / "a.jpg"
        full = self.build([self.metadata(path)], [{
            "anchor_path": str(path),
            "dup_path": os.path.relpath(path),
        }])
        self.assertEqual(full["images"], ["a.jpg"])


if __name__ == "__main__":
    unittest.main()
