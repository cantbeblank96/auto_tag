"""把 questions.examples 里的一条路径展开成实际参考样图。

每个档位只保存一条路径，后写的覆盖先写的。路径是图片时用这一张；
路径是文件夹时，按文件名字典序最多读取 2 张合法图片（先当前目录，再子目录）。
"""
from __future__ import annotations

import os
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple

from auto_tag.core.utils.path_utils import normalize_fs_path

# 能作为 VLM 参考图直接打开的常见图片。原始 YUV 需要宽高，不在此列。
EXAMPLE_IMAGE_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".gif",
    ".tif",
    ".tiff",
)

EXAMPLE_IMAGES_PER_VALUE = 2


def resolve_example_path(path: str) -> str:
    """解析样图路径：绝对路径直接用；相对路径基于 config.json 所在目录。

    随后按当前系统规范化（Windows 盘符与 /mnt/<盘> 互转）。
    """
    raw = str(path or "").strip()
    if not raw:
        return ""
    p = os.path.expanduser(raw)
    if not os.path.isabs(p):
        from auto_tag.core.config import config_json_path

        p = os.path.normpath(os.path.join(os.path.dirname(config_json_path), p))
    return normalize_fs_path(p)


def is_example_image_name(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in EXAMPLE_IMAGE_SUFFIXES


def example_source_path(raw: Any) -> str:
    """一个档位只保留一条路径。若误存成列表，以后一条非空路径覆盖前面的。"""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (list, tuple)):
        last = ""
        for item in raw:
            text = str(item or "").strip()
            if text:
                last = text
        return last
    return ""


def list_images_in_directory(root: str) -> List[str]:
    """文件夹内合法图片。顺序：先当前目录文件名，再子目录；各层按 casefold 字典序。"""
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        for name in filenames:
            if is_example_image_name(name):
                found.append(os.path.join(dirpath, name))
    return found


def _norm_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def iter_example_candidates(sources: Sequence[str]) -> Iterator[Tuple[str, Optional[str]]]:
    """按 sources 顺序产出候选。

    每个元素是 (来源原文, 解析后的图片路径)。来源不存在、不是图片也不是目录、
    或目录里没有合法图片时，图片路径为 None（不占名额，供调用方告警）。
    """
    for src in sources:
        resolved = resolve_example_path(src)
        if not resolved:
            yield src, None
            continue
        if os.path.isdir(resolved):
            images = list_images_in_directory(resolved)
            if not images:
                yield src, None
                continue
            for image in images:
                yield src, image
            continue
        if os.path.isfile(resolved) and is_example_image_name(resolved):
            yield src, resolved
            continue
        yield src, None


def planned_example_rows(questions: Any) -> List[Tuple[str, str, str, List[str]]]:
    """按 questions 整理本次会用到的参考图。

    每项为 (问题键, 档位值, 配置路径, 实际选用的图片路径)。
    文件夹只保留最多 2 张合法图片，与标注时的选取规则一致。
    """
    rows: List[Tuple[str, str, str, List[str]]] = []
    if not isinstance(questions, dict):
        return rows
    for qkey, details in questions.items():
        if not isinstance(details, dict):
            continue
        examples = details.get("examples")
        if not isinstance(examples, dict):
            continue
        for value, raw in examples.items():
            source = example_source_path(raw)
            paths, _truncated = take_example_images([source] if source else [])
            rows.append((str(qkey), str(value), source, paths))
    return rows


def take_example_images(
    sources: Sequence[str],
    *,
    limit: int = EXAMPLE_IMAGES_PER_VALUE,
    accept: Optional[Callable[[str], bool]] = None,
) -> Tuple[List[str], bool]:
    """按添加顺序取最多 limit 张。

    accept 返回 False 的路径（例如文件损坏、读不出来）不占名额，继续往后取。
    第二个返回值表示后面还有能用的图，但已被上限截掉。
    """
    chosen: List[str] = []
    seen = set()
    for _src, image in iter_example_candidates(sources):
        if not image:
            continue
        key = _norm_key(image)
        if key in seen:
            continue
        seen.add(key)
        if accept is not None and not accept(image):
            continue
        if len(chosen) >= limit:
            return chosen, True
        chosen.append(image)
    return chosen, False
