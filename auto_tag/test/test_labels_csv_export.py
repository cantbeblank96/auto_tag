"""可读标签 CSV：真实映射、表格结构、编码及下载方式。"""
from __future__ import annotations

import codecs
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from auto_tag.core import labels_csv_export as csv_export
from auto_tag.core import compact_labels_export as compact
from auto_tag.backend.routers import database


def fixture() -> dict:
    return {
        "images": ["nested/a.jpg", "b.jpg", "empty.jpg"],
        "prefix_id": [1, 1, 0],
        "labels_id": [7, 2, 838],
        "prefix": {0: "", 1: "/data/图片/"},
        "labels": {
            2: {"scene": "室外", "brightness": 8},
            7: {"scene": "室内", "brightness": 0, "person": "female"},
            838: {},
        },
    }


def read_csv(body: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"), newline="")))


class TestLabelsCSV(unittest.TestCase):
    def test_expands_each_image_with_id_as_third_column(self) -> None:
        body = csv_export.compact_labels_csv(fixture(), question_keys=["scene", "brightness"])
        self.assertTrue(body.startswith(codecs.BOM_UTF8))
        self.assertEqual(read_csv(body), [
            ["origin_path", "image_name", "label_id", "scene", "brightness", "person"],
            ["/data/图片/nested/a.jpg", "a.jpg", "7", "室内", "0", "female"],
            ["/data/图片/b.jpg", "b.jpg", "2", "室外", "8", ""],
            ["empty.jpg", "empty.jpg", "838", "", "", ""],
        ])

    def test_decoded_json_dict_keys_and_windows_paths(self) -> None:
        full = json.loads(json.dumps(fixture()))
        full["prefix"]["1"] = "D:\\图片\\"
        full["images"][0] = "nested\\a.jpg"
        rows = read_csv(csv_export.compact_labels_csv(full))
        self.assertEqual(rows[1][:3], ["D:\\图片\\nested\\a.jpg", "a.jpg", "7"])
        self.assertEqual(rows[1][rows[0].index("scene")], "室内")

    def test_csv_quotes_and_complex_values_preserve_content(self) -> None:
        full = {
            "images": ['带,逗号"的图.jpg'], "prefix_id": [0], "labels_id": [0],
            "prefix": {0: "/data/"},
            "labels": {0: {"text": '中文,"引号"\n下一行', "flag": False, "missing": None,
                           "nested": {"a": [1, "中文"]}}},
        }
        rows = read_csv(csv_export.compact_labels_csv(full))
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["text"], '中文,"引号"\n下一行')
        self.assertEqual(row["flag"], "false")
        self.assertEqual(row["missing"], "")
        self.assertEqual(json.loads(row["nested"]), {"a": [1, "中文"]})
        self.assertEqual(row["image_name"], '带,逗号"的图.jpg')

    def test_label_columns_cannot_overwrite_path_or_id(self) -> None:
        full = {"images": ["a.jpg"], "prefix_id": [0], "labels_id": [1],
                "prefix": {0: "/data/"}, "labels": {1: {"label_id": "business-id", "label.label_id": "another", "origin_path": "tag"}}}
        rows = read_csv(csv_export.compact_labels_csv(full))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertEqual(rows[1][:3], ["/data/a.jpg", "a.jpg", "1"])
        self.assertCountEqual(rows[1][3:], ["business-id", "another", "tag"])

    def test_empty_export_still_has_configured_columns(self) -> None:
        full = {"images": [], "prefix_id": [], "labels_id": [], "prefix": {}, "labels": {}}
        self.assertEqual(read_csv(csv_export.compact_labels_csv(full, question_keys=["scene"])),
                         [["origin_path", "image_name", "label_id", "scene"]])

    def test_inconsistent_arrays_or_missing_references_do_not_silently_drop_rows(self) -> None:
        for field in ("labels_id", "prefix_id"):
            full = fixture()
            full[field].pop()
            with self.subTest(field=field), self.assertRaises(ValueError):
                csv_export.compact_labels_csv(full)
        for field, key in (("labels", 7), ("prefix", 1)):
            full = fixture()
            del full[field][key]
            with self.subTest(field=field), self.assertRaises(ValueError):
                csv_export.compact_labels_csv(full)


class TestCSVDelivery(unittest.TestCase):
    def test_browser_download_has_csv_headers_and_full_contents(self) -> None:
        with mock.patch.object(compact, "build_compact_export", return_value=fixture()), \
             mock.patch.object(database.settings, "questions", {"scene": {}, "brightness": {}}):
            response = database.export_labels_csv(work_dir=None, output_dir=None)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn('filename="auto_tag_labels.csv"', response.headers["content-disposition"])
        self.assertEqual(read_csv(response.body)[0][:3], ["origin_path", "image_name", "label_id"])
        self.assertEqual(len(read_csv(response.body)), 4)

    def test_local_directory_receives_same_csv_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(compact, "build_compact_export", return_value=fixture()):
                result = database.export_labels_csv(work_dir=None, output_dir=folder)
            body = Path(result["path"]).read_bytes()
            self.assertEqual(result["filename"], "auto_tag_labels.csv")
            self.assertEqual(result["bytes"], len(body))
            self.assertTrue(body.startswith(codecs.BOM_UTF8))
            self.assertEqual(len(read_csv(body)), 4)

    def test_invalid_source_is_reported_as_bad_request(self) -> None:
        full = fixture()
        full["labels_id"] = []
        with mock.patch.object(compact, "build_compact_export", return_value=full):
            with self.assertRaises(HTTPException) as raised:
                database.export_labels_csv(work_dir=None, output_dir=None)
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
