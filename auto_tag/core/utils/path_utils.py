import os
from typing import List


def path_variants(p: str) -> List[str]:
    """返回输入路径及其 realpath 变体，用于模糊匹配。"""
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