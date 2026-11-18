#!/usr/bin/env python3
"""三个坏例类别各抽 5 张，用当前参考图策略测标注准确率。

目录名按「旧结果-标准答案」解读。数字标签的右侧带小数（如 2-1.0），
标准答案取整数部分。head_pose 右侧就是类别名。

抽样种子固定为 20261118。每个类别从数量最多的若干混淆目录里各取一张，
face_size 只有 3 个目录，因此 2-1.0 和 1-0.0 各两张、1-2.0 一张。

用法（仓库根目录）:
    uv run python tests/debug_accuracy_sample5.py
"""
from __future__ import annotations

import importlib.util
import json
import logging
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BAD = ROOT / "data" / "10.29 bad case"
RUN_DIR = ROOT / "debug_from_cursor" / "accuracy_sample5"
SEED = 20261118
PER_CATEGORY = 5
# 每个类别优先覆盖的混淆目录，再按种子补齐。
PREFERRED = {
    "head_pose": [
        "slight_pitch-frontal",
        "frontal-slight_pitch",
        "slight_pitch-slight_yaw",
        "frontal-slight_yaw",
        "large_pitch_up-slight_pitch",
    ],
    "face_distance": ["1-2.0", "2-3.0", "0-2.0", "0-1.0", "1-3.0"],
    "face_size": ["2-1.0", "1-0.0", "1-2.0", "2-1.0", "1-0.0"],
}


def _load_dbg():
    path = ROOT / "tests" / "debug_one_case_three_labels.py"
    spec = importlib.util.spec_from_file_location("debug_one_case_three_labels", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def images_in(folder: Path) -> list[Path]:
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    ]
    return sorted(files, key=lambda p: p.name.casefold())


def parse_pair(category: str, folder: str) -> tuple[str, str]:
    left, right = folder.split("-", 1)
    if category == "head_pose":
        return left, right
    return left, str(int(float(right)))


def norm_label(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def choose_samples() -> list[dict]:
    rng = random.Random(SEED)
    picked = []
    for category, folders in PREFERRED.items():
        used = set()
        for folder_name in folders:
            folder = BAD / category / folder_name
            files = [p for p in images_in(folder) if str(p) not in used]
            if not files:
                continue
            path = rng.choice(files)
            used.add(str(path))
            old, gt = parse_pair(category, folder_name)
            picked.append(
                {
                    "category": category,
                    "folder": folder_name,
                    "old_label": old,
                    "gt": gt,
                    "path": str(path),
                }
            )
    return picked


def tool_lines(turn: dict) -> list[str]:
    lines = []
    messages = turn.get("messages") or []
    if not messages:
        return lines
    content = messages[0].get("content")
    if not isinstance(content, list):
        return lines
    for part in content:
        if part.get("type") != "text":
            continue
        text = part.get("text") or ""
        if text.startswith("[Tool measurement") or text.startswith("[Reference measurement"):
            lines.append(text.split("\n", 1)[-1][:500] if "\n" in text else text[:500])
    return lines


def main() -> None:
    dbg = _load_dbg()
    dbg.RUN_DIR = RUN_DIR
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(RUN_DIR / "runtime.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    samples = choose_samples()
    (RUN_DIR / "samples.json").write_text(
        json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    from auto_tag.core.circuit_breaker import get_circuit_breaker
    from auto_tag.core.config import settings
    from auto_tag.core.utils.load_image import load_image
    from auto_tag.core.vlm_client import resolve_max_tokens_for_model

    settings.vlm_http_timeout = dbg.DEBUG_HTTP_TIMEOUT
    dbg.apply_local_examples(settings.questions)
    vlm = dbg.create_vlm()
    model = vlm.models[0] if vlm.models else {}
    traces: list[dict] = []
    dbg.install_trace(traces)

    rows = []
    for sample in samples:
        get_circuit_breaker().reset_all()
        bucket = {"key": sample["category"], "turns": []}
        traces.append(bucket)
        image = load_image(sample["path"])
        call = dbg.annotate_one(vlm, image, sample["category"])
        if not call.get("ok"):
            time.sleep(45)
            get_circuit_breaker().reset_all()
            bucket = {"key": sample["category"], "turns": []}
            traces.append(bucket)
            call = dbg.annotate_one(vlm, image, sample["category"])
        pred = norm_label(call.get("returned_value"))
        row = {
            **sample,
            "image_size": list(image.size),
            "pred": pred,
            "correct": bool(call.get("ok") and pred == sample["gt"]),
            "call": call,
            "tool_notes": tool_lines(bucket["turns"][-1]) if bucket["turns"] else [],
            "raw": [
                t.get("raw_content")
                for t in bucket["turns"]
                if t.get("raw_content")
            ],
        }
        rows.append(row)
        print(
            f"{row['category']}\t{row['folder']}\tgt={row['gt']}\tpred={row['pred']}\t"
            f"ok={call.get('ok')}\tcorrect={row['correct']}\t{Path(row['path']).name}",
            flush=True,
        )
        (RUN_DIR / "results.json").write_text(
            json.dumps(
                {
                    "seed": SEED,
                    "max_tokens": resolve_max_tokens_for_model(model) if model else None,
                    "assumption": "文件夹名左侧是旧结果，右侧是标准答案",
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    write_summary(rows, resolve_max_tokens_for_model(model) if model else None)


def write_summary(rows: list[dict], max_tokens) -> None:
    lines = [
        "# 三类别各 5 张准确率",
        "",
        "标准答案取目录名右侧。数字档的 `1.0` 记为 `1`。",
        f"max_tokens: {max_tokens}",
        "",
    ]
    for category in ("head_pose", "face_distance", "face_size"):
        group = [r for r in rows if r["category"] == category]
        scored = [r for r in group if r["call"].get("ok")]
        correct = sum(1 for r in scored if r["correct"])
        lines.append(f"## {category}")
        lines.append("")
        if scored:
            lines.append(f"有效 {len(scored)} 张，正确 {correct} 张，准确率 {correct / len(scored):.0%}。")
        else:
            lines.append("没有成功返回的标注。")
        lines.append("")
        lines.append("| 目录 | 标准答案 | 预测 | 对错 | 文件 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in group:
            mark = "对" if r["correct"] else ("失败" if not r["call"].get("ok") else "错")
            lines.append(
                f"| `{r['folder']}` | `{r['gt']}` | `{r['pred'] or '—'}` | {mark} | `{Path(r['path']).name}` |"
            )
        lines.append("")
        for r in group:
            lines.append(f"### {Path(r['path']).name}")
            lines.append("")
            lines.append(f"原文: {r['raw']}")
            for note in r["tool_notes"]:
                if note.startswith("{") or "face_count" in note or "yaw" in note:
                    lines.append("")
                    lines.append(f"工具: `{note[:400]}`")
            lines.append("")
    scored = [r for r in rows if r["call"].get("ok")]
    correct = sum(1 for r in scored if r["correct"])
    lines.insert(4, f"合计有效 {len(scored)} / {len(rows)}，正确 {correct}，准确率 {correct / len(scored):.0%}。" if scored else "合计没有成功样本。")
    (RUN_DIR / "accuracy.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"LOG {RUN_DIR / 'accuracy.md'}", flush=True)


if __name__ == "__main__":
    main()
