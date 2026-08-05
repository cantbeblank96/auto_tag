"""标注工具注册表与执行：通用「工具注入」框架的执行层。

设计（v0.0.6，飞书文档 3.2 节方向的通用化）：
- 每个工具 = 名称 + 描述（供 VLM agent 分析与 prompt 展示）+ 执行器（图片 → 结构化 dict）；
- 问题级绑定（questions[key].tools）与全局开关（annotation_tools.<name>.enabled）双层控制，
  标注时取「本次 keys 涉及的工具 ∩ 全局可用工具」；
- 工具结果以**纯文本 JSON** 注入 VLM 消息（见 vlm_client._messages_with_image），
  最终标签仍由 VLM 综合图片与测量信息判断，工具不做硬覆盖；
- 工具不可用（SDK 缺失/证书失效/模型未配置）时跳过并告警，行为退化为无工具。

当前基于 kevin_sdk 注册三个工具：
- face_detect：人脸检测（数量、主脸 bbox 与占比）
- head_pose：头部姿态角 yaw/pitch/roll（依赖 detect + align 关键点）
- face_attribute：人脸属性分类（依赖 detect + align 关键点）

eye_state 暂不注册：kevin_sdk 上游标注「TODO 有 bug，未跑通」，待其修复后再加。
"""
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── 工具注册表 ──────────────────────────────────────────
# description 同时用于：① VLM agent 分析「问题需要哪些工具」；② 前端工具管理页展示。

TOOL_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "face_detect",
        "display_name": "人脸检测",
        "description": (
            "检测图片中的人脸，返回人脸数量与最大人脸的位置框（bbox，像素坐标）、"
            "人脸高度占画面高度比例（height_ratio）、面积占比（area_ratio）与置信度。"
            "适用于人脸大小、人脸数量、人物距离、构图位置等相关问题。"
        ),
        "requires_align": False,
    },
    {
        "name": "head_pose",
        "display_name": "头部姿态",
        "description": (
            "估计最大人脸的头部姿态角：yaw（左右转头）、pitch（抬低头）、roll（歪头），单位度。"
            "适用于正脸/侧脸、头部朝向、低头/抬头等相关问题。依赖人脸检测与关键点。"
        ),
        "requires_align": True,
    },
    {
        "name": "face_attribute",
        "display_name": "人脸属性",
        "description": (
            "预测最大人脸的属性（如性别、年龄、眼镜、胡子、微笑等，具体维度取决于属性模型），"
            "返回各属性的类别判定。适用于性别、年龄、眼镜、胡子等相关问题。"
            "依赖人脸检测与关键点。"
        ),
        "requires_align": True,
    },
]

_REGISTRY_BY_NAME = {t["name"]: t for t in TOOL_REGISTRY}

# ── SDK 句柄与线程安全 ──────────────────────────────────
# kevin_sdk 为 C 库，全部调用在全局锁内串行（检测/关键点均为毫秒级，影响可忽略）。
_SDK_LOCK = threading.Lock()
_HANDLES: Dict[str, Any] = {}
_SDK_IMPORT_FAILED = False


def _tools_cfg() -> Dict[str, Any]:
    from auto_tag.core.config import settings

    cfg = getattr(settings, "annotation_tools", None)
    return cfg if isinstance(cfg, dict) else {}


def _tool_cfg(name: str) -> Dict[str, Any]:
    cfg = _tools_cfg().get(name)
    return cfg if isinstance(cfg, dict) else {}


def _sdk_importable() -> Tuple[bool, str]:
    global _SDK_IMPORT_FAILED
    if _SDK_IMPORT_FAILED:
        return False, "kevin_sdk 不可用"
    try:
        import kevin_sdk  # noqa: F401

        return True, ""
    except Exception as e:
        _SDK_IMPORT_FAILED = True
        return False, f"kevin_sdk 导入失败: {e}"


def _build_handle(key: str, model_type: str, model_path: str):
    """懒加载并缓存单个模型句柄；失败抛异常由调用方处理。"""
    if key in _HANDLES:
        return _HANDLES[key]
    from kevin_sdk.api import build_model

    handle = build_model(model_type=model_type, model_path=model_path)
    _HANDLES[key] = handle
    logger.info("annotation_tools: model loaded [%s] %s", key, model_path)
    return handle


def _detect_handle():
    cfg = _tool_cfg("face_detect")
    path = str(cfg.get("model_path") or "")
    return _build_handle("face_detect", "detect", path)


def _align_handles() -> List[Any]:
    paths = _tools_cfg().get("align_model_paths") or []
    assert isinstance(paths, (list, tuple)) and len(paths) == 2, (
        "annotation_tools.align_model_paths 需配置 [360align, align] 两个模型路径"
    )
    return [
        _build_handle("360align", "360align", str(paths[0])),
        _build_handle("align", "align", str(paths[1])),
    ]


def _pose_handle():
    cfg = _tool_cfg("head_pose")
    return _build_handle("head_pose", "pose", str(cfg.get("model_path") or ""))


def _attribute_handle():
    cfg = _tool_cfg("face_attribute")
    return _build_handle("face_attribute", "attribute", str(cfg.get("model_path") or ""))


# ── 可用性检查 ──────────────────────────────────────────


def _paths_ok(paths: List[str]) -> bool:
    return bool(paths) and all(os.path.isfile(str(p)) for p in paths)


def tool_status(name: str) -> Dict[str, Any]:
    """工具当前状态：enabled / available / reason（供 API 与前端展示）。"""
    spec = _REGISTRY_BY_NAME.get(name)
    if spec is None:
        return {"name": name, "available": False, "reason": "未注册的工具"}
    cfg = _tool_cfg(name)
    enabled = bool(cfg.get("enabled"))
    reasons: List[str] = []

    ok, why = _sdk_importable()
    if not ok:
        reasons.append(why)
    if not cfg.get("model_path"):
        reasons.append("未配置模型路径 model_path")
    elif not os.path.isfile(str(cfg.get("model_path"))):
        reasons.append("模型文件不存在")
    if spec["requires_align"]:
        align_paths = _tools_cfg().get("align_model_paths") or []
        if not _paths_ok(list(align_paths)):
            reasons.append("align_model_paths 未配置或文件缺失")

    available = enabled and not reasons
    return {
        "name": name,
        "display_name": spec["display_name"],
        "description": spec["description"],
        "enabled": enabled,
        "available": available,
        "reason": "；".join(reasons) if reasons else "",
        "model_path": str(cfg.get("model_path") or ""),
    }


def list_tool_status() -> List[Dict[str, Any]]:
    return [tool_status(t["name"]) for t in TOOL_REGISTRY]


def enabled_tool_names() -> List[str]:
    """全局可用工具名列表（问题级绑定需是其子集）。"""
    return [s["name"] for s in list_tool_status() if s["available"]]


# ── 执行层 ──────────────────────────────────────────────


def _pil_to_bgr(image) -> np.ndarray:
    arr = np.asarray(image.convert("RGB"))
    return arr[:, :, ::-1]


def _detect_main_face(bgr: np.ndarray) -> Optional[Dict[str, Any]]:
    """检测最大人脸；无人脸返回 None。"""
    from kevin_sdk.api import detect

    min_score = float(_tool_cfg("face_detect").get("min_score", 0.5) or 0)
    results = detect(image=bgr, model=_detect_handle())
    if not results:
        return None
    h, w = bgr.shape[:2]
    best, best_area = None, -1.0
    for it in results:
        b = it.get("bbox") or {}
        bw = int(b.get("right", 0)) - int(b.get("left", 0))
        bh = int(b.get("bottom", 0)) - int(b.get("top", 0))
        if bw <= 0 or bh <= 0 or float(it.get("score") or 0) < min_score:
            continue
        area = bw * bh
        if area > best_area:
            best_area, best = area, {"bbox": b, "score": float(it["score"]), "bw": bw, "bh": bh}
    if best is None:
        return None
    return {
        "bbox": best["bbox"],
        "score": round(best["score"], 4),
        "height_ratio": round(best["bh"] / float(h), 4),
        "area_ratio": round(best_area / float(w * h), 4),
    }


def _get_landmarks(bgr: np.ndarray, face: Dict[str, Any]) -> Dict[str, Any]:
    """detect → align（360align + align）获取 106 点关键点。"""
    from kevin_sdk.api import align

    return align(
        image=bgr,
        model=_align_handles(),
        bbox=face["bbox"],
        b_parse_result=True,
    )


def run_tools_for_image(image, tool_names: List[str]) -> Dict[str, Any]:
    """对一张图执行工具列表，返回 {tool_name: 结果 dict}。

    image: PIL.Image；tool_names 已按全局可用性过滤。单个工具失败记 error 字段，
    不影响其他工具；detect 结果在同批工具间共享（head_pose/face_attribute 复用）。
    """
    names = [n for n in (tool_names or []) if n in _REGISTRY_BY_NAME]
    if not names:
        return {}
    try:
        bgr = _pil_to_bgr(image)
    except Exception as e:
        return {n: {"error": f"图像转换失败: {e}"} for n in names}

    out: Dict[str, Any] = {}
    face: Optional[Dict[str, Any]] = None
    landmarks: Optional[Any] = None
    face_done = False

    def ensure_face() -> Optional[Dict[str, Any]]:
        nonlocal face, face_done
        if not face_done:
            face = _detect_main_face(bgr)
            face_done = True
        return face

    for name in names:
        try:
            with _SDK_LOCK:
                if name == "face_detect":
                    f = ensure_face()
                    if f is None:
                        out[name] = {"face_count": 0, "main_face": None}
                    else:
                        out[name] = {"face_count": 1, "main_face": f}
                else:
                    f = ensure_face()
                    if f is None:
                        out[name] = {"error": "未检测到人脸"}
                        continue
                    if landmarks is None:
                        landmarks = _get_landmarks(bgr, f)
                    if name == "head_pose":
                        from kevin_sdk.api import pose

                        angles = pose(landmarks=landmarks, model=_pose_handle())
                        out[name] = {
                            k: round(float(v), 2) for k, v in angles.items()
                        }
                    elif name == "face_attribute":
                        from kevin_sdk.api import attribute

                        res = attribute(
                            image=bgr,
                            model=_attribute_handle(),
                            landmarks=landmarks,
                            outputs_mapper={"Age": "weighted_sum", None: "argmax"},
                        )
                        out[name] = {
                            it["attr_name"]: (
                                round(float(it["predict"]), 2)
                                if isinstance(it.get("predict"), float)
                                else it.get("predict")
                            )
                            for it in res
                        }
        except Exception as e:
            logger.warning("annotation_tools: %s 执行失败: %s", name, e)
            out[name] = {"error": str(e)[:200]}
    return out
