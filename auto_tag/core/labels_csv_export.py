"""将紧凑导出的路径与标签字典展开为每图一行的可读 CSV。"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable


def _lookup(mapping: dict, key: Any, field: str) -> Any:
    # 内存导出为整数键，JSON 读回后为字符串键。
    if key in mapping:
        return mapping[key]
    if str(key) in mapping:
        return mapping[str(key)]
    raise ValueError(f"紧凑导出缺少 {field} 映射：{key}")


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (bool, dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def compact_labels_csv(full: dict[str, Any], *, question_keys: Iterable[str] = ()) -> bytes:
    """展开完整 compact 数据；返回带 UTF-8 BOM 的 CSV，兼容 Excel 中文。"""
    images = full.get("images") or []
    label_ids = full.get("labels_id") or []
    prefix_ids = full.get("prefix_id") or []
    if len(images) != len(label_ids) or len(images) != len(prefix_ids):
        raise ValueError("紧凑导出的 images、labels_id、prefix_id 长度不一致")
    labels = full.get("labels") or {}
    prefixes = full.get("prefix") or {}
    if not isinstance(labels, dict) or not isinstance(prefixes, dict):
        raise ValueError("紧凑导出的 labels 和 prefix 必须是字典")

    actual_keys: set[str] = set()
    for label in labels.values():
        if not isinstance(label, dict) or any(not isinstance(key, str) for key in label):
            raise ValueError("紧凑导出的每个 labels 项必须是以标签名称为键的字典")
        actual_keys.update(label)
    # Questions 顺序优先，历史标注中的额外字段也不能遗漏。
    label_keys = list(dict.fromkeys(str(key) for key in question_keys))
    label_keys.extend(sorted(actual_keys.difference(label_keys)))

    header = ["origin_path", "image_name", "label_id"]
    used = set(header)
    for key in label_keys:
        column = key
        while column in used:
            column = f"label.{column}"
        header.append(column)
        used.add(column)

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(header)
    for image, prefix_id, label_id in zip(images, prefix_ids, label_ids):
        prefix = _lookup(prefixes, prefix_id, "prefix")
        label = _lookup(labels, label_id, "labels")
        if not isinstance(prefix, str) or not isinstance(image, str):
            raise ValueError("紧凑导出的图片路径和路径前缀必须是字符串")
        # prefix 已包含末尾分隔符；不要用当前主机的路径规则改写另一平台的路径。
        origin_path = prefix + image
        image_name = origin_path.replace("\\", "/").rsplit("/", 1)[-1]
        writer.writerow([origin_path, image_name, label_id, *(_cell(label.get(key)) for key in label_keys)])
    return output.getvalue().encode("utf-8-sig")
