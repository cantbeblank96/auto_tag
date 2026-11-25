#!/usr/bin/env python3
"""对 data/hunhe 的 1000 张图只标注 head_pose、face_distance、face_size。

密钥和商汤地址用本机 auto_tag/config.json。问题定义、参考图和工具开关用
data/config_11_23.json，路径只在进程内改写：
- D:\\dev\\sample_image -> data/sample_image
- /mnt/d/dev/kevin_sdk/models -> /mnt/faceid/share/to_zihan/models

不走聚类。每张图一次请求，只带这三个问题。结果可中断后续跑。

用法（仓库根目录）：
    uv run python tests/run_three_labels_1000.py
"""
from __future__ import annotations

import csv
import json
import logging
import sys
import time
import traceback
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEYS = ("head_pose", "face_distance", "face_size")
IMAGE_DIR = ROOT / "data" / "hunhe"
QUESTION_CONFIG = ROOT / "data" / "config_11_23.json"
HUMAN_XLSX = ROOT / "data" / "human_auto_compare_1029.xlsx"
SAMPLE_ROOT = ROOT / "data" / "sample_image"
MODEL_ROOT = Path("/mnt/faceid/share/to_zihan/models")
RUN_DIR = ROOT / "debug_from_cursor" / "three_labels_1000"
JSONL = RUN_DIR / "predictions.jsonl"
OUT_XLSX = RUN_DIR / "three_labels_compare.xlsx"
OUT_ACCURACY = RUN_DIR / "accuracy.md"
HTTP_TIMEOUT = 180.0


def remap_sample_path(raw: str) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    marker = "D:/dev/sample_image"
    if text.lower().startswith(marker.lower()):
        rest = text[len(marker) :].lstrip("/")
        return str(SAMPLE_ROOT / rest)
    return str(raw or "").strip()


def remap_model_path(raw: str) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    marker = "/kevin_sdk/models/"
    if marker in text:
        rest = text.split(marker, 1)[1]
        return str(MODEL_ROOT / rest)
    return str(raw or "").strip()


def norm_label(key: str, value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if key in ("face_size", "face_distance"):
        try:
            number = float(text)
        except ValueError:
            number = None
        if number is not None and number.is_integer():
            text = str(int(number))
    if key == "head_pose":
        text = text.replace(" ", "_")
    return text


def load_human_rows() -> list[dict[str, str]]:
    import xml.etree.ElementTree as ET

    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(HUMAN_XLSX) as book:
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
    rows = sheet.findall("m:sheetData/m:row", ns)

    def cell_text(cell) -> str:
        node = cell.find("m:is/m:t", ns)
        if node is not None and node.text is not None:
            return node.text
        value = cell.find("m:v", ns)
        return value.text if value is not None and value.text is not None else ""

    def letters(ref: str) -> str:
        return "".join(ch for ch in ref if ch.isalpha())

    header = {
        letters(cell.attrib["r"]): cell_text(cell)
        for cell in rows[0].findall("m:c", ns)
    }
    index = {name: col for col, name in header.items()}
    needed = ["origin_path", "image_name", *[f"human_{key}" for key in KEYS]]
    missing = [name for name in needed if name not in index]
    if missing:
        raise SystemExit(f"人类标签表缺少列: {missing}")
    out = []
    for row in rows[1:]:
        cells = {
            letters(cell.attrib["r"]): cell_text(cell)
            for cell in row.findall("m:c", ns)
        }
        item = {name: cells.get(index[name], "") for name in needed}
        item["file_name"] = Path(item["origin_path"].replace("\\", "/")).name or item["image_name"]
        out.append(item)
    return out


def configure() -> None:
    from auto_tag.core.config import settings

    payload = json.loads(QUESTION_CONFIG.read_text(encoding="utf-8"))
    questions = payload.get("questions")
    if not isinstance(questions, dict):
        raise SystemExit("config_11_23.json 没有 questions")
    for key in KEYS:
        details = questions.get(key)
        if not isinstance(details, dict) or not isinstance(details.get("examples"), dict):
            raise SystemExit(f"{key} 缺少 examples")
        details["examples"] = {
            str(value): remap_sample_path(raw)
            for value, raw in details["examples"].items()
        }
    tools = payload.get("annotation_tools")
    if not isinstance(tools, dict):
        raise SystemExit("config_11_23.json 没有 annotation_tools")
    for name, cfg in list(tools.items()):
        if isinstance(cfg, dict) and cfg.get("model_path"):
            cfg["model_path"] = remap_model_path(cfg["model_path"])
    if isinstance(tools.get("align_model_paths"), list):
        tools["align_model_paths"] = [remap_model_path(p) for p in tools["align_model_paths"]]
    object.__setattr__(settings, "questions", questions)
    object.__setattr__(settings, "annotation_tools", tools)
    object.__setattr__(settings, "vlm_http_timeout", HTTP_TIMEOUT)


def load_done() -> dict[str, dict]:
    done = {}
    if not JSONL.is_file():
        return done
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        done[row["file_name"]] = row
    return done


def annotate_three(vlm, image) -> dict:
    from auto_tag.core.config import settings

    existing = {key: None for key in settings.questions if key not in KEYS}
    started = time.perf_counter()
    try:
        result = vlm.annotate_image_incremental(image, existing)
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_s": round(time.perf_counter() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "pred": {key: "" for key in KEYS},
        }
    pred = {}
    for key in KEYS:
        pred[key] = "" if not isinstance(result, dict) else result.get(key)
    return {
        "ok": True,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "error": "",
        "pred": pred,
    }


def write_xlsx(rows: list[dict]) -> None:
    headers = [
        "origin_path",
        "image_name",
        "auto_head_pose",
        "human_head_pose",
        "auto_face_distance",
        "human_face_distance",
        "auto_face_size",
        "human_face_size",
        "ok",
        "error",
    ]

    def col_name(index: int) -> str:
        name = ""
        n = index
        while n:
            n, rem = divmod(n - 1, 26)
            name = chr(65 + rem) + name
        return name

    body = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for r_index, values in enumerate([headers, *rows], start=1):
        cells = []
        for c_index, value in enumerate(values, start=1):
            ref = f"{col_name(c_index)}{r_index}"
            text = escape("" if value is None else str(value))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        body.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    body.append("</sheetData></worksheet>")
    sheet = "".join(body)
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="compare" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    with zipfile.ZipFile(OUT_XLSX, "w", compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", content_types)
        book.writestr("_rels/.rels", rels)
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        book.writestr("xl/worksheets/sheet1.xml", sheet)


def write_outputs(humans: list[dict], done: dict[str, dict]) -> None:
    table = []
    scored = {key: {"hit": 0, "compared": 0} for key in KEYS}
    failed = 0
    missing_pred = 0
    for human in humans:
        pred_row = done.get(human["file_name"])
        preds = (pred_row or {}).get("pred") or {}
        ok = bool(pred_row and pred_row.get("ok"))
        if pred_row and not ok:
            failed += 1
        if pred_row is None:
            missing_pred += 1
        values = [human["origin_path"], human["image_name"]]
        for key in KEYS:
            auto = norm_label(key, preds.get(key))
            gold = norm_label(key, human[f"human_{key}"])
            values.extend([auto, gold])
            if pred_row and ok and auto and gold:
                scored[key]["compared"] += 1
                scored[key]["hit"] += int(auto == gold)
        values.extend(["1" if ok else "0", (pred_row or {}).get("error", "")])
        table.append(values)
    write_xlsx(table)
    lines = ["# 三个标签准确率", ""]
    lines.append(f"- 人类标签图片: {len(humans)}")
    lines.append(f"- 已有预测: {len(done)}")
    lines.append(f"- 调用失败: {failed}")
    lines.append(f"- 尚未预测: {missing_pred}")
    lines.append("")
    lines.append("比较前把 face_size、face_distance 的 `1.0` 收成 `1`，head_pose 的空格收成下划线。只统计调用成功且两边都有值的图片。")
    lines.append("")
    lines.append("| 标签 | 正确 | 可比较 | 准确率 |")
    lines.append("| --- | --- | --- | --- |")
    for key in KEYS:
        hit = scored[key]["hit"]
        total = scored[key]["compared"]
        rate = f"{hit / total:.2%}" if total else "-"
        lines.append(f"| {key} | {hit} | {total} | {rate} |")
    OUT_ACCURACY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (RUN_DIR / "three_labels_compare.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "origin_path", "image_name",
            "auto_head_pose", "human_head_pose",
            "auto_face_distance", "human_face_distance",
            "auto_face_size", "human_face_size",
            "ok", "error",
        ])
        writer.writerows(table)


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(RUN_DIR / "runtime.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    configure()
    humans = load_human_rows()
    by_name = {row["file_name"]: row for row in humans}
    images = sorted(path for path in IMAGE_DIR.iterdir() if path.is_file())
    missing = [path.name for path in images if path.name not in by_name]
    if missing:
        raise SystemExit(f"有 {len(missing)} 张图在人类标签表里找不到，例如 {missing[:3]}")
    done = {name: row for name, row in load_done().items() if row.get("ok")}
    from auto_tag.core.circuit_breaker import CircuitBreakerConfig, get_circuit_breaker
    from auto_tag.core.config import settings
    from auto_tag.core.utils.load_image import load_image
    from auto_tag.core.vlm_client import VLMClient

    models = list(getattr(settings, "vlm_models", None) or [])
    if not models:
        raise SystemExit("本机 config.json 没有可用的 vlm_models")
    seen_keys = {str(item.get("api_key") or "") for item in models}
    extra_models = json.loads(QUESTION_CONFIG.read_text(encoding="utf-8")).get("vlm_models") or []
    for item in extra_models:
        key = str(item.get("api_key") or "")
        if key and key not in seen_keys:
            models.append(item)
            seen_keys.add(key)
    logging.info("本次使用 %d 个商汤端点，429 后会换下一个", len(models))
    breaker = get_circuit_breaker()
    breaker.update_config(
        CircuitBreakerConfig(
            time_window_seconds=settings.circuit_breaker_time_window,
            failure_rate_threshold=settings.circuit_breaker_failure_threshold,
            cooldown_seconds=settings.circuit_breaker_cooldown,
        )
    )
    vlm = VLMClient(models=models, circuit_breaker=breaker)
    logging.info("待标注 %d 张，已完成 %d 张", len(images), len(done))
    with JSONL.open("a", encoding="utf-8") as handle:
        for index, path in enumerate(images, start=1):
            if path.name in done:
                continue
            started = time.perf_counter()
            try:
                image = load_image(str(path))
            except Exception as exc:
                record = {
                    "file_name": path.name,
                    "ok": False,
                    "elapsed_s": round(time.perf_counter() - started, 3),
                    "error": f"load: {type(exc).__name__}: {exc}",
                    "pred": {key: "" for key in KEYS},
                }
            else:
                attempt = 0
                while True:
                    attempt += 1
                    record = annotate_three(vlm, image)
                    record["file_name"] = path.name
                    if record["ok"] or "429" not in record.get("error", ""):
                        break
                    wait_s = min(180, 60 * attempt)
                    logging.warning(
                        "%s 仍被 TPM/RPM 限制，%d 秒后再试同一张（第 %d 次）",
                        path.name,
                        wait_s,
                        attempt,
                    )
                    time.sleep(wait_s)
            done[path.name] = record
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            logging.info(
                "[%d/%d] %s ok=%s %.1fs %s",
                index,
                len(images),
                path.name,
                record["ok"],
                record["elapsed_s"],
                record["pred"] if record["ok"] else record["error"][:180],
            )
            if index % 10 == 0 or index == len(images):
                write_outputs(humans, done)
    write_outputs(humans, done)
    logging.info("完成。结果在 %s", OUT_XLSX)


if __name__ == "__main__":
    main()
