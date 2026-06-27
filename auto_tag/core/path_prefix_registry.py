"""work_dir/log 下维护路径前缀注册表，索引与侧车仅存 prefix_id + 相对后缀。"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "path_prefix_registry.json"


class PathPrefixRegistry:
    """prefix_id（字符串键）→ 绝对目录前缀（带末尾分隔符）。0 表示空前缀（整路径存在 rel 中）。"""

    def __init__(self, log_dir: str) -> None:
        self.log_dir = log_dir
        self._file = os.path.join(log_dir, REGISTRY_FILENAME)
        self.prefixes: Dict[str, str] = {"0": ""}
        self._next_id = 1
        os.makedirs(log_dir, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self._file):
            return
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            p = raw.get("prefixes") or {}
            if isinstance(p, dict) and p:
                self.prefixes = {str(k): str(v) for k, v in p.items()}
            self._next_id = int(raw.get("next_id", 1))
            if "0" not in self.prefixes:
                self.prefixes["0"] = ""
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning("Failed to load path prefix registry: %s", e)

    def save(self) -> None:
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(
                    {"next_id": self._next_id, "prefixes": self.prefixes},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError as e:
            logger.warning("Failed to save path prefix registry: %s", e)

    def register_abs_dir(self, abs_dir: str) -> str:
        """注册输入根目录，返回 prefix_id 字符串。"""
        d = os.path.realpath(os.path.abspath(os.path.expanduser(abs_dir.strip())))
        if not os.path.isdir(d):
            return "0"
        if not d.endswith(os.sep):
            d = d + os.sep
        for pid, pref in self.prefixes.items():
            if pref == d:
                return str(pid)
        pid = str(self._next_id)
        self._next_id += 1
        self.prefixes[pid] = d
        self.save()
        return pid

    def split(self, full_path: str) -> Tuple[str, str]:
        """最长匹配已注册前缀，返回 (prefix_id, rel_path)。"""
        p = os.path.realpath(os.path.abspath(os.path.expanduser(str(full_path).strip())))
        best_pid = "0"
        best_len = -1
        for pid, pref in self.prefixes.items():
            if not pref:
                continue
            if p.startswith(pref) and len(pref) > best_len:
                best_len = len(pref)
                best_pid = str(pid)
        if best_len <= 0:
            return "0", p
        rel = p[best_len:].lstrip(os.sep)
        return best_pid, rel

    def compose(self, prefix_id: str, rel_path: str) -> str:
        pid = str(prefix_id)
        pref = self.prefixes.get(pid, "")
        if not pref:
            return os.path.realpath(os.path.abspath(os.path.expanduser(str(rel_path))))
        rel = str(rel_path).lstrip(os.sep)
        return os.path.realpath(os.path.join(pref, rel))


def resolve_stored_image_path(meta: Dict[str, object], registry: PathPrefixRegistry) -> str:
    """从 metadata 解析绝对路径（新字段优先，否则旧 image_path）。"""
    if meta.get("path_prefix_id") is not None and meta.get("image_rel_path") is not None:
        return registry.compose(str(meta["path_prefix_id"]), str(meta["image_rel_path"]))
    ip = meta.get("image_path")
    if ip:
        return os.path.realpath(os.path.abspath(os.path.expanduser(str(ip))))
    return ""
