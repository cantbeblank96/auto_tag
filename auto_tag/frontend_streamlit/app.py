"""
Streamlit 前端：通过 HTTP 调用 auto_tag.backend FastAPI。

全局 work_dir 与 config.json 在「设置」页；任务页只配置与目录无关的参数。
侧边栏切换页面 + persist_* 保留任务表单状态。
"""
from __future__ import annotations

import json
import math
import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import streamlit as st

from api_client import AutoTagApiClient

DEFAULT_API = os.environ.get("AUTO_TAG_API_BASE", "http://127.0.0.1:8000")

AUTO_TAG_DIR = Path(__file__).resolve().parent.parent
# 默认：auto_tag/work_dir、auto_tag/config.json（均存为绝对路径字符串）
DEFAULT_WORK_DIR_ABS = str((AUTO_TAG_DIR / "work_dir").resolve())
DEFAULT_CONFIG_PATH_ABS = str((AUTO_TAG_DIR / "config.json").resolve())
DEFAULT_ENV_PATH_ABS = str((AUTO_TAG_DIR / ".env").resolve())

ROTATE_OPTIONS: list[tuple[str, Optional[str]]] = [
    ("不旋转", None),
    ("顺时针 90° (ROTATE_90_CLOCKWISE)", "ROTATE_90_CLOCKWISE"),
    ("180° (ROTATE_180)", "ROTATE_180"),
    ("逆时针 90° (ROTATE_90_COUNTERCLOCKWISE)", "ROTATE_90_COUNTERCLOCKWISE"),
]

YUV_TYPES = ["nv21", "nv12", "yuv420p"]
TASK_JSON_VERSION = 1

TASK_FIELD_DEFAULTS: List[tuple[str, Any]] = [
    ("task_input_dirs", ""),
    ("task_rot_label", ROTATE_OPTIONS[0][0]),
    ("task_mixed_yuv", False),
    ("task_b_yuv", False),
    ("task_yuv_w", 640),
    ("task_yuv_h", 480),
    ("task_yuv_type", "nv21"),
]

APP_VERSION = "0.0"
AUTHOR_EMAIL = "xukaiming1996@163.com"
# 升级该版本会强制将「跳过库中已有路径」重置为默认勾选（修复旧会话持久化 False）
_UI_SKIP_DEFAULTS_VERSION = 4

STATUS_LABEL = {
    "queued": "排队中",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
}


# 全局设置集中存 dict（参考 kevin_apps 里 state_s 写法）；持久值勿绑在仅「设置」页才渲染的 widget key 上。
_APP_SETTINGS_DEFAULT: Dict[str, Any] = {
    "work_dir": DEFAULT_WORK_DIR_ABS,
    "config_path": DEFAULT_CONFIG_PATH_ABS,
    "config_text": None,
    "env_path": DEFAULT_ENV_PATH_ABS,
    "env_text": None,
    "skip_if_in_db": True,
}


def _app_settings() -> Dict[str, Any]:
    s = st.session_state.get("app_settings")
    if not isinstance(s, dict):
        s = dict(_APP_SETTINGS_DEFAULT)
        st.session_state.app_settings = s
    else:
        st.session_state.app_settings = s
        for k, v in _APP_SETTINGS_DEFAULT.items():
            if k not in s:
                s[k] = v
    if not str(s.get("work_dir", "")).strip():
        s["work_dir"] = DEFAULT_WORK_DIR_ABS
    if not str(s.get("config_path", "")).strip():
        s["config_path"] = DEFAULT_CONFIG_PATH_ABS
    if not str(s.get("env_path", "")).strip():
        s["env_path"] = DEFAULT_ENV_PATH_ABS
    return s


def _flush_settings_ui_to_app_if_present() -> None:
    """main 早期调用：切页后下一轮开始时若 ui_* 仍在 session 中，先写回 app_settings。"""
    app = _app_settings()
    if "ui_work_dir" in st.session_state:
        app["work_dir"] = str(st.session_state.ui_work_dir)
    if "ui_config_path" in st.session_state:
        app["config_path"] = str(st.session_state.ui_config_path)
    if "ui_config_text" in st.session_state:
        app["config_text"] = st.session_state.ui_config_text
    if "ui_env_path" in st.session_state:
        app["env_path"] = str(st.session_state.ui_env_path)
    if "ui_env_text" in st.session_state:
        app["env_text"] = st.session_state.ui_env_text
    if "ui_skip_if_in_db" in st.session_state:
        app["skip_if_in_db"] = bool(st.session_state.ui_skip_if_in_db)


def _pkey(wk: str) -> str:
    return f"persist_{wk}"


def _resolved_work_dir_str() -> str:
    """当前设置中的 work_dir，规范为绝对路径。"""
    raw = _app_settings().get("work_dir") or DEFAULT_WORK_DIR_ABS
    return str(Path(str(raw).strip()).expanduser().resolve())


def _resolved_config_path() -> Path:
    """当前设置中的 config.json 路径，规范为绝对路径。"""
    raw = _app_settings().get("config_path") or DEFAULT_CONFIG_PATH_ABS
    return Path(str(raw).strip()).expanduser().resolve()


def _sort_keys_recursive(obj: Any) -> Any:
    """展示用：递归按 key 排序，避免 JSON 顺序杂乱。"""
    if isinstance(obj, dict):
        return {k: _sort_keys_recursive(obj[k]) for k in sorted(obj.keys(), key=str)}
    if isinstance(obj, list):
        return [_sort_keys_recursive(x) for x in obj]
    return obj


_DB_SNAPSHOT_COMPARE_KEYS = (
    "tau_dup",
    "tau_cls",
    "batch_size",
    "collection_name",
    "clip_model_name",
    "vlm_model_name",
    "questions",
    "duplicate_links_filename",
    "embedding_subdir",
)


def _db_scalar_same(key: str, a: Any, b: Any) -> bool:
    if key in (
        "collection_name",
        "clip_model_name",
        "vlm_model_name",
        "duplicate_links_filename",
        "embedding_subdir",
    ):
        return str(a) == str(b)
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


def _db_snapshot_identical(snap: Dict[str, Any], curp: Dict[str, Any]) -> bool:
    """与后端 stats 差异逻辑一致：比较关键标量 + questions。"""
    for key in _DB_SNAPSHOT_COMPARE_KEYS:
        if key == "questions":
            sq = json.dumps(
                _sort_keys_recursive(snap.get("questions") or {}),
                sort_keys=True,
                ensure_ascii=True,
            )
            cq = json.dumps(
                _sort_keys_recursive(curp.get("questions") or {}),
                sort_keys=True,
                ensure_ascii=True,
            )
            if sq != cq:
                return False
        else:
            if not _db_scalar_same(key, snap.get(key), curp.get(key)):
                return False
    return True


def _st_json_sorted(obj: Any) -> None:
    st.json(_sort_keys_recursive(obj))


def _fmt_count_ratio(num: int, den: int) -> str:
    if den <= 0:
        return str(num)
    return f"{num} ({num / den:.1%})"


def _resolved_env_path() -> Path:
    raw = _app_settings().get("env_path") or DEFAULT_ENV_PATH_ABS
    return Path(str(raw).strip()).expanduser().resolve()


def _reload_env_text_from_disk() -> None:
    p = _resolved_env_path()
    app = _app_settings()
    if p.is_file():
        app["env_text"] = p.read_text(encoding="utf-8")
    else:
        app["env_text"] = "# auto_tag 环境变量（KEY=value）\n"


def _reload_config_text_from_disk() -> None:
    """写入 app_settings['config_text']；须在绑定 key=ui_config_text 的 text_area 之前调用。"""
    p = _resolved_config_path()
    app = _app_settings()
    if p.is_file():
        app["config_text"] = p.read_text(encoding="utf-8")
    else:
        app["config_text"] = "{\n}\n"


def _ensure_session_defaults() -> None:
    defaults: Dict[str, Any] = {
        "sidebar_api_base": DEFAULT_API,
        "main_nav": "任务",
        "task_job_queue": [],
        "queue_runner_armed": False,
        "rec_offset": 0,
        "rec_limit": 30,
        "rec_cid": "",
        "rec_work_dir": "",
    }
    for wk, dv in TASK_FIELD_DEFAULTS:
        pk = _pkey(wk)
        if pk not in st.session_state:
            st.session_state[pk] = dv
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    _app_settings()
    if st.session_state.get("_ui_skip_defaults_version") != _UI_SKIP_DEFAULTS_VERSION:
        st.session_state.ui_skip_if_in_db = True
        st.session_state.app_settings["skip_if_in_db"] = True
        st.session_state["_ui_skip_defaults_version"] = _UI_SKIP_DEFAULTS_VERSION
    elif "ui_skip_if_in_db" not in st.session_state:
        st.session_state.ui_skip_if_in_db = bool(
            st.session_state.app_settings.get("skip_if_in_db", True)
        )


def _sync_widgets_from_persist() -> None:
    for wk, dv in TASK_FIELD_DEFAULTS:
        if wk not in st.session_state:
            st.session_state[wk] = st.session_state.get(_pkey(wk), dv)


def _persist_cb(wk: str) -> Callable[[], None]:
    def _fn() -> None:
        st.session_state[_pkey(wk)] = st.session_state[wk]

    return _fn


def _task_only_dict_from_persist() -> Dict[str, Any]:
    rot_label = st.session_state.get(_pkey("task_rot_label"), ROTATE_OPTIONS[0][0])
    rotate_val = dict(ROTATE_OPTIONS).get(rot_label)
    dirs_raw = st.session_state.get(_pkey("task_input_dirs"), "")
    dirs = [x.strip() for x in str(dirs_raw).splitlines() if x.strip()]
    return {
        "version": TASK_JSON_VERSION,
        "input_dirs": dirs,
        "rotate_angle": rotate_val,
        "b_yuv_image": bool(st.session_state.get(_pkey("task_b_yuv"), False)),
        "mixed_yuv": bool(st.session_state.get(_pkey("task_mixed_yuv"), False)),
        "yuv_type": str(st.session_state.get(_pkey("task_yuv_type"), "nv21")),
        "image_width": int(st.session_state.get(_pkey("task_yuv_w"), 640)),
        "image_height": int(st.session_state.get(_pkey("task_yuv_h"), 480)),
    }


def _task_dict_for_export() -> Dict[str, Any]:
    """下载用任务 JSON：不含 work_dir（全局在「设置」页）。"""
    d = _task_only_dict_from_persist()
    d["skip_if_in_db"] = bool(_app_settings().get("skip_if_in_db", True))
    return d


def _task_payload_for_api(task_only: Dict[str, Any]) -> Dict[str, Any]:
    wd = _resolved_work_dir_str()
    return {
        "input_dirs": task_only["input_dirs"],
        "image_ls_files": [],
        "work_dir": wd,
        "rotate_angle": task_only["rotate_angle"],
        "b_yuv_image": task_only["b_yuv_image"],
        "mixed_yuv": task_only["mixed_yuv"],
        "yuv_type": task_only["yuv_type"],
        "image_width": task_only["image_width"],
        "image_height": task_only["image_height"],
        "skip_if_in_db": bool(_app_settings().get("skip_if_in_db", True)),
    }


def _write_persist_from_form_dict(d: Dict[str, Any]) -> None:
    st.session_state[_pkey("task_input_dirs")] = d.get("input_dirs_text", "")
    st.session_state[_pkey("task_rot_label")] = d.get("rot_label", ROTATE_OPTIONS[0][0])
    st.session_state[_pkey("task_mixed_yuv")] = d.get("mixed_yuv", False)
    st.session_state[_pkey("task_b_yuv")] = d.get("b_yuv", False)
    st.session_state[_pkey("task_yuv_w")] = d.get("yuv_w", 640)
    st.session_state[_pkey("task_yuv_h")] = d.get("yuv_h", 480)
    st.session_state[_pkey("task_yuv_type")] = d.get("yuv_type", "nv21")
    for wk, _ in TASK_FIELD_DEFAULTS:
        st.session_state.pop(wk, None)


def _apply_task_json_to_session(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("根节点须为 JSON 对象")

    ids = data.get("input_dirs")
    if isinstance(ids, list):
        text = "\n".join(str(x) for x in ids)
    elif isinstance(ids, str):
        text = ids
    else:
        text = ""

    yt = str(data.get("yuv_type", "nv21"))
    if yt not in YUV_TYPES:
        yt = "nv21"
    try:
        yw = int(data.get("image_width", 640))
    except (TypeError, ValueError):
        yw = 640
    try:
        yh = int(data.get("image_height", 480))
    except (TypeError, ValueError):
        yh = 480

    _write_persist_from_form_dict(
        {
            "input_dirs_text": text,
            "rot_label": _rot_label_from_angle(data.get("rotate_angle")),
            "mixed_yuv": bool(data.get("mixed_yuv", False)),
            "b_yuv": bool(data.get("b_yuv_image", False)),
            "yuv_w": yw,
            "yuv_h": yh,
            "yuv_type": yt,
        }
    )
    if "skip_if_in_db" in data:
        _app_settings()["skip_if_in_db"] = bool(data["skip_if_in_db"])
        st.session_state.ui_skip_if_in_db = bool(data["skip_if_in_db"])


def _rot_label_from_angle(angle: Optional[str]) -> str:
    if not angle:
        return ROTATE_OPTIONS[0][0]
    for lab, val in ROTATE_OPTIONS:
        if val == angle:
            return lab
    return ROTATE_OPTIONS[0][0]


def _queue_step(client: AutoTagApiClient) -> None:
    if not st.session_state.get("queue_runner_armed", False):
        return
    q: List[Dict[str, Any]] = st.session_state.task_job_queue
    if not q:
        st.session_state.queue_runner_armed = False
        return

    for i, item in enumerate(q):
        if item["status"] == "running" and item.get("server_job_id"):
            try:
                j = client.get_job(item["server_job_id"])
                q[i]["last_job"] = j
                stt = j.get("status")
                if stt == "done":
                    q[i]["status"] = "completed"
                elif stt == "failed":
                    q[i]["status"] = "failed"
                    q[i]["error"] = j.get("error") or "failed"
            except Exception as e:
                q[i]["status"] = "failed"
                q[i]["error"] = str(e)
            return

    for i, item in enumerate(q):
        if item["status"] != "queued":
            continue
        full = _task_payload_for_api(item["payload"])
        try:
            r = client.create_job(full)
            q[i]["server_job_id"] = r.get("job_id")
            q[i]["status"] = "running"
            q[i]["work_dir_effective"] = _resolved_work_dir_str()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                return
            q[i]["status"] = "failed"
            q[i]["error"] = e.response.text[:500] if e.response else str(e)
        except Exception as e:
            q[i]["status"] = "failed"
            q[i]["error"] = str(e)
        return

    if not any(x["status"] in ("queued", "running") for x in q):
        st.session_state.queue_runner_armed = False


def main() -> None:
    st.set_page_config(page_title="auto_tag", layout="wide")
    _ensure_session_defaults()
    _flush_settings_ui_to_app_if_present()

    with st.sidebar:
        st.markdown("# auto_tag 控制台")
        st.caption("标注流水线 Web 控制台")
        st.divider()
        api_base = st.text_input("API 根地址", key="sidebar_api_base")
        client = AutoTagApiClient(api_base)
        nav = st.radio(
            "页面",
            ["任务", "图片查询", "数据库", "设置", "其他"],
            key="main_nav",
        )

    if nav == "任务":
        st.header("标注任务")
        _render_task_page(client)
    elif nav == "图片查询":
        st.header("图片查询")
        _render_image_query_tab(client)
    elif nav == "数据库":
        st.header("数据库")
        _render_database_page(client)
    elif nav == "其他":
        st.header("其他")
        _render_other_page(client)
    else:
        st.header("设置")
        _render_settings_page()


def _render_task_page(client: AutoTagApiClient) -> None:
    _sync_widgets_from_persist()

    st.caption(
        f"当前全局 **work_dir**（解析后绝对路径）：`{_resolved_work_dir_str()}`"
    )

    st.subheader("加载 & 保存")
    c_dl, c_up = st.columns(2)
    with c_dl:
        blob = json.dumps(_task_dict_for_export(), ensure_ascii=False, indent=2)
        st.download_button(
            "保存并下载任务 JSON",
            data=blob.encode("utf-8"),
            file_name="auto_tag_job.json",
            mime="application/json",
        )
    with c_up:
        uploaded = st.file_uploader(
            "上传 JSON 加载到表单",
            type=["json"],
            key="task_json_uploader",
        )
        if uploaded is not None and st.button("将上传的 JSON 应用到表单", key="apply_task_json_btn"):
            try:
                data = json.loads(uploaded.read().decode("utf-8"))
                _apply_task_json_to_session(data)
                st.success("已加载到表单；请检查后在「新建」中点击「确认」加入队列")
                st.rerun()
            except Exception as e:
                st.error(f"解析失败: {e}")

    st.divider()
    st.subheader("新建")

    st.text_area(
        "输入目录（每行一个绝对路径）",
        placeholder="/path/to/images",
        key="task_input_dirs",
        height=120,
        on_change=_persist_cb("task_input_dirs"),
    )
    st.selectbox(
        "rotate_angle（可选）",
        [x[0] for x in ROTATE_OPTIONS],
        key="task_rot_label",
        on_change=_persist_cb("task_rot_label"),
    )

    st.markdown("**YUV 相关设置**")
    st.checkbox(
        "混合目录（.nv21/.nv12/.yuv 按 YUV 读，其余按图）",
        key="task_mixed_yuv",
        on_change=_persist_cb("task_mixed_yuv"),
    )
    st.checkbox(
        "整批均为 YUV（与「混合目录」二选一通常只开其一）",
        key="task_b_yuv",
        on_change=_persist_cb("task_b_yuv"),
    )
    c_w, c_h, c_t = st.columns(3)
    with c_w:
        st.number_input("YUV 宽度", min_value=0, key="task_yuv_w", on_change=_persist_cb("task_yuv_w"))
    with c_h:
        st.number_input("YUV 高度", min_value=0, key="task_yuv_h", on_change=_persist_cb("task_yuv_h"))
    with c_t:
        st.selectbox("YUV 类型", YUV_TYPES, key="task_yuv_type", on_change=_persist_cb("task_yuv_type"))

    if st.button("确认", key="btn_confirm_task", type="primary"):
        for wk, _ in TASK_FIELD_DEFAULTS:
            _persist_cb(wk)()
        body = _task_only_dict_from_persist()
        if not body["input_dirs"]:
            st.warning("请至少填写一个输入目录")
        else:
            summ = body["input_dirs"][0]
            if len(body["input_dirs"]) > 1:
                summ += f" 等{len(body['input_dirs'])}项"
            st.session_state.task_job_queue.append(
                {
                    "queue_id": str(uuid.uuid4())[:8],
                    "summary": summ,
                    "work_dir_snapshot": _resolved_work_dir_str(),
                    "status": "queued",
                    "server_job_id": None,
                    "error": None,
                    "payload": body,
                }
            )
            st.success("已加入任务队列，请在「运行」中提交执行")

    st.divider()
    st.subheader("运行")
    st.checkbox(
        "跳过库中已有路径（队列中所有任务统一生效，默认勾选）",
        key="ui_skip_if_in_db",
        help="勾选后若索引中已有相同路径则跳过；不勾选则先删旧记录再重新处理。",
    )
    _flush_settings_ui_to_app_if_present()
    _task_run_section_fragment(client)


@st.fragment(run_every=timedelta(seconds=2))
def _task_run_section_fragment(client: AutoTagApiClient) -> None:
    """运行区 fragment：定时拉取队列与 job 状态（跳过选项在父级页面，避免 fragment 内状态不同步）。"""
    _flush_settings_ui_to_app_if_present()
    _queue_step(client)

    q: List[Dict[str, Any]] = st.session_state.task_job_queue
    has_any = len(q) > 0
    has_queued = any(x["status"] == "queued" for x in q)

    if has_any:
        try:
            import pandas as pd

            rows = []
            for item in q:
                lj = item.get("last_job") or {}
                total = int(lj.get("total") or 0)
                proc = int(lj.get("processed") or 0)
                fail = int(lj.get("failed_so_far") or 0)
                if item["status"] == "completed":
                    fail = int(lj.get("failed_count") or fail)
                sk_db = int(lj.get("skip_in_db") or 0)
                vlm = int(lj.get("vlm_calls") or 0)
                s1 = int(lj.get("stage1_skips") or 0)
                s2 = int(lj.get("stage2_joins") or 0)
                skip_all = sk_db + s1 + s2
                den = proc if proc > 0 else 0
                rows.append(
                    {
                        "队列ID": item["queue_id"],
                        "摘要": item.get("summary", ""),
                        "状态": STATUS_LABEL.get(item["status"], item["status"]),
                        "已收集": total,
                        "已处理": _fmt_count_ratio(proc, total) if total > 0 else proc,
                        "打标数": _fmt_count_ratio(vlm, den) if den > 0 else vlm,
                        "跳过数": _fmt_count_ratio(skip_all, den) if den > 0 else skip_all,
                        "失败数": _fmt_count_ratio(fail, den) if den > 0 else fail,
                        "server_job_id": item.get("server_job_id") or "",
                        "错误": (item.get("error") or "")[:120],
                    }
                )
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        except Exception:
            st.table(
                [{"队列ID": it["queue_id"], "状态": it["status"]} for it in q]
            )

        for item in q:
            if item.get("status") == "running":
                lj = item.get("last_job") or {}
                tot = int(lj.get("total") or 0)
                proc = int(lj.get("processed") or 0)
                if tot > 0:
                    st.caption(
                        f"运行中 `{item['queue_id']}`：已处理 {proc} / 已收集 {tot}，"
                        f"失败 {lj.get('failed_so_far', 0)}"
                    )
                    st.progress(min(proc / tot, 1.0))

        for item in q:
            if item.get("status") == "running" and item.get("server_job_id"):
                jid = str(item["server_job_id"])
                with st.expander(
                    f"任务日志（队列 {item['queue_id']} · job {jid[:12]}…）",
                    expanded=True,
                ):
                    try:
                        lines = client.get_job_logs(jid, tail=400)
                        st.code("\n".join(lines[-100:]), language="text")
                    except Exception as e:
                        st.warning(str(e))
                break

    if st.button(
        "提交任务",
        key="btn_submit_queue",
        type="primary",
        disabled=not has_queued,
        help="按队列依次提交；执行时使用「设置」中当前的 work_dir",
    ):
        st.session_state.queue_runner_armed = True
        st.rerun()

    if not has_any:
        st.caption("尚无已确认的任务；请先在「新建」中填写并点击「确认」加入队列。")
    elif has_any and not has_queued and not st.session_state.get(
        "queue_runner_armed", False
    ):
        st.caption("当前队列中无「排队中」项。执行时使用的 work_dir 以「设置」页为准。")


def _render_image_query_tab(client: AutoTagApiClient) -> None:
    st.caption(
        "按 **image_path** 查询：优先 **向量索引**；若无记录再查 **log** 下近重复侧车表（不占索引空间）。"
    )
    wd = _resolved_work_dir_str()
    st.text_input("图片绝对路径", key="query_image_path")
    qp = (st.session_state.get("query_image_path") or "").strip()
    st.caption("若未入库且为 YUV，可在下方填写解码参数后再点预览。")
    c1, c2, c3 = st.columns(3)
    with c1:
        qw = st.number_input("预览 YUV 宽", min_value=0, value=640, key="query_yuv_w")
    with c2:
        qh = st.number_input("预览 YUV 高", min_value=0, value=480, key="query_yuv_h")
    with c3:
        qyt = st.selectbox("预览 YUV 类型", YUV_TYPES, key="query_yuv_type")
    b_yuv_prev = st.checkbox("强制按 YUV 解码预览", key="query_b_yuv")

    if st.button("查询", key="btn_image_query", type="primary"):
        if not qp:
            st.warning("请填写路径")
        else:
            try:
                info = client.record_by_path(qp, work_dir=wd)
                st.session_state["_last_image_query"] = info
                st.session_state.pop("_last_preview_png", None)
            except Exception as e:
                st.error(str(e))

    info = st.session_state.get("_last_image_query")
    if isinstance(info, dict) and info.get("found"):
        if info.get("source") == "stage1_duplicate_only":
            st.subheader("近重复侧车（未写入向量索引）")
            st.success("已在侧车表中命中该路径。")
            if info.get("note"):
                st.info(str(info.get("note")))
            _st_json_sorted(
                {
                    "source": info.get("source"),
                    "matched_path": info.get("matched_path"),
                    "duplicate_links": info.get("duplicate_links"),
                }
            )
            anchors = info.get("anchor_embedding_records") or []
            if anchors:
                st.subheader("锚点图在索引中的结果（自动查询）")
                for a in anchors:
                    st.markdown(f"**锚点路径**：`{a.get('anchor_path', '')}`")
                    er = a.get("embedding_record") or {}
                    _st_json_sorted(
                        {
                            k: er[k]
                            for k in (
                                "matched_path",
                                "cluster_id",
                                "is_cluster_center",
                                "cluster_center_path",
                                "own_labels",
                                "effective_labels",
                                "cluster_center_labels",
                            )
                            if k in er
                        }
                    )
            mp = str(info.get("matched_path") or qp)
            if st.button("加载预览图", key="btn_load_preview_dup"):
                try:
                    png = client.fetch_preview_png(
                        mp,
                        work_dir=wd,
                        image_width=int(qw),
                        image_height=int(qh),
                        yuv_type=qyt,
                        b_yuv_image=b_yuv_prev,
                    )
                    st.session_state["_last_preview_png"] = png
                except Exception as e:
                    st.error(str(e))
            st.caption(
                "此类路径不写入向量索引。编辑簇标签请使用上方锚点查询结果中的 **effective_labels**；"
                "若要将本路径作为新中心入库，请用下方「无索引记录」流程插入。"
            )
        else:
            st.subheader("索引记录摘要")
            _st_json_sorted(
                {
                    k: info[k]
                    for k in (
                        "matched_path",
                        "cluster_id",
                        "is_cluster_center",
                        "cluster_center_path",
                        "own_labels",
                        "effective_labels",
                        "cluster_center_labels",
                    )
                    if k in info
                }
            )
            if st.button("加载预览图", key="btn_load_preview"):
                try:
                    png = client.fetch_preview_png(
                        str(info.get("matched_path") or qp),
                        work_dir=wd,
                        image_width=int(qw),
                        image_height=int(qh),
                        yuv_type=qyt,
                        b_yuv_image=b_yuv_prev,
                    )
                    st.session_state["_last_preview_png"] = png
                except Exception as e:
                    st.error(str(e))

            st.subheader("编辑 labels 并写回库")
            st.caption(
                "**整簇同步**：更新该图片及其所属 cluster 内**全部**文档的 labels；"
                "**仅本图**：只更新该路径对应文档的 labels；若该图在库中**无记录**（如 Stage1 重复未入库），"
                "保存时会用 CLIP 提特征并**新增**一条带 labels 的条目（请在上文填好 YUV 解码参数）。"
            )
            mode = st.radio(
                "保存模式",
                ["image_only", "with_cluster"],
                format_func=lambda x: (
                    "仅本图（无记录则新插入）" if x == "image_only" else "整簇同步"
                ),
                horizontal=True,
                key="query_label_mode",
            )
            default_labels = json.dumps(
                info.get("effective_labels") or {}, ensure_ascii=False, indent=2
            )
            labels_text = st.text_area(
                "labels（JSON）", value=default_labels, height=220, key="query_labels_json"
            )
            if st.button("保存到数据库", key="btn_save_labels", type="primary"):
                try:
                    labels_obj = json.loads(labels_text)
                    out = client.update_record_labels(
                        {
                            "work_dir": wd,
                            "image_path": str(info.get("matched_path") or qp),
                            "labels": labels_obj,
                            "mode": mode,
                            "image_width": int(qw),
                            "image_height": int(qh),
                            "yuv_type": qyt,
                            "b_yuv_image": b_yuv_prev,
                        }
                    )
                    st.success(f"已更新：{out}")
                    st.session_state.pop("_last_image_query", None)
                    st.session_state.pop("_last_preview_png", None)
                except Exception as e:
                    st.error(str(e))
    elif isinstance(info, dict) and not info.get("found"):
        st.info(
            "向量索引与近重复侧车均未找到该路径；若文件在服务端磁盘上存在，仍可按下方参数尝试预览。"
        )
        if st.button("加载预览图（仅磁盘）", key="btn_load_preview_nf"):
            if not qp:
                st.warning("请填写路径")
            else:
                try:
                    png = client.fetch_preview_png(
                        qp,
                        work_dir=wd,
                        image_width=int(qw),
                        image_height=int(qh),
                        yuv_type=qyt,
                        b_yuv_image=b_yuv_prev,
                    )
                    st.session_state["_last_preview_png"] = png
                except Exception as e:
                    st.error(str(e))

        st.subheader("无索引记录时直接写入 labels")
        st.caption(
            "选用 **仅本图** 逻辑：后端会读磁盘图片、CLIP 提特征后**新增**一条索引记录（新簇中心）。"
            "YUV 请务必填宽高并勾选「强制按 YUV」或在路径上使用混合目录规则。"
        )
        labels_nf = st.text_area(
            "labels（JSON，插入用）",
            value="{}",
            height=140,
            key="query_labels_insert_nf",
        )
        if st.button("插入带 labels 的新条目", key="btn_insert_labels_nf", type="primary"):
            if not qp:
                st.warning("请填写路径并先点「查询」")
            else:
                try:
                    obj = json.loads(labels_nf)
                    out = client.update_record_labels(
                        {
                            "work_dir": wd,
                            "image_path": qp,
                            "labels": obj,
                            "mode": "image_only",
                            "image_width": int(qw),
                            "image_height": int(qh),
                            "yuv_type": qyt,
                            "b_yuv_image": b_yuv_prev,
                        }
                    )
                    st.success(f"已处理：{out}")
                    st.session_state.pop("_last_image_query", None)
                    st.session_state.pop("_last_preview_png", None)
                except Exception as e:
                    st.error(str(e))

    png = st.session_state.get("_last_preview_png")
    if png:
        st.image(png, caption="预览")


def _db_subsection_title(title: str) -> None:
    """数据库页内「查询 / 导出」下的分区标题。

    Streamlit 的 ``st.subheader`` 与 Markdown ``###`` 同为 HTML h3，看起来同级；
    此处用更小字号的 h4，保证明显低于上一级 ``st.subheader``。
    """
    st.markdown(
        f'<h4 style="font-size: 1.05rem; font-weight: 600; margin: 0.65rem 0 0.3rem 0; '
        f'line-height: 1.35; color: inherit;">{title}</h4>',
        unsafe_allow_html=True,
    )


def _db_work_dir_for_api() -> Optional[str]:
    rwd = str(st.session_state.rec_work_dir).strip()
    gwd = _resolved_work_dir_str()
    return rwd or gwd or None


def _render_database_page(client: AutoTagApiClient) -> None:
    st.caption(
        "同一 **work_dir** 下：向量索引目录、**log** 中的构建快照与近重复侧车表；"
        "「覆盖 work_dir」与下方查询区共用。刷新状态时会读取「设置」里 **config.json 路径** 用于与快照比对。"
    )
    st.subheader("状态")
    if st.button("刷新状态", key="btn_db_refresh_stats"):
        try:
            cfg_p = str(_resolved_config_path())
            st.session_state["_db_last_stats"] = client.database_stats(
                work_dir=_db_work_dir_for_api(),
                config_path=cfg_p,
            )
        except Exception as e:
            st.error(str(e))

    stats = st.session_state.get("_db_last_stats")
    if isinstance(stats, dict):
        try:
            import pandas as pd

            n_emb = int(
                stats.get("embedding_record_count")
                or stats.get("chroma_document_count")
                or 0
            )
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("索引内图片条数", n_emb)
            c2.metric("簇数量", int(stats.get("cluster_count") or 0))
            c3.metric("含非空标注的条数", int(stats.get("labeled_document_count") or 0))
            c4.metric("近重复对条数（侧车）", int(stats.get("duplicate_link_rows") or 0))
            snap = stats.get("snapshot")
            curp = stats.get("current_params") or {}
            cfg_identical = bool(
                snap
                and isinstance(snap, dict)
                and isinstance(curp, dict)
                and _db_snapshot_identical(snap, curp)
            )
            snap_view = None
            if isinstance(snap, dict):
                snap_view = {
                    k: snap.get(k)
                    for k in (
                        "recorded_at",
                        "tau_dup",
                        "tau_cls",
                        "batch_size",
                        "collection_name",
                        "clip_model_name",
                        "vlm_model_name",
                        "questions",
                        "duplicate_links_filename",
                        "embedding_subdir",
                    )
                }
            col_sn_l, col_sn_r = st.columns(2)
            with col_sn_l:
                st.markdown("**上次成功任务写入的快照（构建/标注参数）**")
                log_d = str(stats.get("log_dir") or "").strip()
                if log_d:
                    snap_fp = os.path.join(log_d, "auto_tag_db_build_snapshot.json")
                    try:
                        snap_disp = str(Path(snap_fp).resolve())
                    except Exception:
                        snap_disp = snap_fp
                    st.caption(f"已读取：`{snap_disp}`")
                if snap_view:
                    with st.expander("查看 JSON", expanded=not cfg_identical):
                        _st_json_sorted(snap_view)
                else:
                    st.warning("尚无 auto_tag_db_build_snapshot.json。")
            with col_sn_r:
                st.markdown("**当前配置**")
                eff = stats.get("config_path_effective")
                if eff:
                    st.caption(f"已读取：`{eff}`")
                with st.expander("查看 JSON", expanded=not cfg_identical):
                    _st_json_sorted(curp)
            if cfg_identical and snap_view:
                st.caption("完全一致。")
        except Exception as e:
            st.warning(str(e))

    st.subheader("更新")
    if not isinstance(stats, dict):
        st.caption("请先点击「刷新状态」。")
    else:
        rows = stats.get("param_diff_table") or []
        if rows:
            try:
                import pandas as pd

                st.dataframe(
                    pd.DataFrame(rows), hide_index=True, use_container_width=True
                )
            except Exception:
                _st_json_sorted(rows)
        en_recompute = bool(stats.get("enable_recompute_relations"))
        en_ann = bool(stats.get("enable_reannotate"))
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button(
                "完全重建索引",
                key="btn_db_rebuild_rel",
                help=(
                    "清空索引与侧车，按快照 input_dirs 重跑完整流水线（CLIP + VLM 等），"
                    "与排队任务互斥；无快照或缺少 input_dirs 时会报错。"
                ),
            ):
                try:
                    _st_json_sorted(
                        client.database_rebuild_relations(
                            work_dir=_db_work_dir_for_api()
                        )
                    )
                except Exception as e:
                    st.error(str(e))
        with c2:
            if st.button(
                "仅重算关系",
                key="btn_db_recompute_rel",
                disabled=not en_recompute,
                help=(
                    "复用索引中已有向量与 labels，按当前 τ_dup/τ_cls 重算簇与侧车，不调用 VLM/CLIP；"
                    "处理顺序为路径字典序。删侧车前会按路径备份，重算后再合并仍满足当前 τ_dup 且锚点仍在索引中的旧行，"
                    "并刷新 anchor_id。仅在「关系类参数」与快照不一致时可点（与排队任务互斥）。"
                ),
            ):
                try:
                    _st_json_sorted(
                        client.database_recompute_relations(
                            work_dir=_db_work_dir_for_api()
                        )
                    )
                except Exception as e:
                    st.error(str(e))
        with c3:
            st.radio(
                "标注更新模式（二选一）",
                ["full", "incremental"],
                format_func=lambda x: (
                    "全量：按当前 questions 整图重标" if x == "full" else "增量：仅为缺失的键补充"
                ),
                horizontal=True,
                key="db_anno_mode",
                disabled=not en_ann,
            )
            st.checkbox(
                "仅簇中心调 VLM",
                key="db_anno_centers",
                disabled=not en_ann,
            )
            if st.button(
                "更新标注（questions）",
                key="btn_db_reannotate",
                disabled=not en_ann,
            ):
                try:
                    mode = str(st.session_state.get("db_anno_mode") or "full")
                    _st_json_sorted(
                        client.database_reannotate(
                            {
                                "work_dir": _db_work_dir_for_api(),
                                "full_refresh": mode == "full",
                                "incremental": mode == "incremental",
                                "centers_only": bool(
                                    st.session_state.get("db_anno_centers", False)
                                ),
                            }
                        )
                    )
                except Exception as e:
                    st.error(str(e))

    st.subheader("查询")
    _db_subsection_title("索引记录")
    _render_records_tab(client)

    _db_subsection_title("近重复对（侧车）")
    _render_dup_tab(client)

    st.subheader("导出")
    _db_subsection_title("数据库")
    exp_wd = _db_work_dir_for_api()
    max_btns = 40
    _EXP_MAX = 200_000
    n_emb_tot = (
        int(
            (stats or {}).get("embedding_record_count")
            or (stats or {}).get("chroma_document_count")
            or 0
        )
        if isinstance(stats, dict)
        else 0
    )
    dup_tot = int((stats or {}).get("duplicate_link_rows") or 0) if isinstance(stats, dict) else 0

    tab_db_emb, tab_db_dup = st.tabs(["索引", "侧车"])

    with tab_db_emb:
        tab_em_r, tab_em_c, tab_em_k = st.tabs(
            ["按 offset/limit", "按 cluster", "分块"]
        )

        with tab_em_r:
            e_off = st.number_input(
                "offset", min_value=0, value=0, key="exp_emb_range_off"
            )
            e_lim = st.number_input(
                "limit",
                min_value=1,
                max_value=_EXP_MAX,
                value=_EXP_MAX,
                key="exp_emb_range_lim",
            )
            if st.button("请求此段（仅索引记录）", key="btn_exp_emb_range"):
                try:
                    st.session_state["_exp_emb_range"] = client.export_embeddings(
                        work_dir=exp_wd,
                        mode="range",
                        offset=int(e_off),
                        limit=int(e_lim),
                    )
                except Exception as e:
                    st.error(str(e))
            br = st.session_state.get("_exp_emb_range")
            if br:
                st.download_button(
                    "下载 JSON",
                    data=br,
                    file_name=f"auto_tag_embeddings_range_{e_off}_{e_lim}.json",
                    mime="application/json",
                    key="dl_exp_emb_range",
                )
        with tab_em_c:
            ecid = st.text_input("cluster_id", key="exp_emb_cluster_id")
            if st.button("请求该 cluster（仅索引）", key="btn_exp_emb_cluster"):
                if not str(ecid).strip():
                    st.warning("请填写 cluster_id")
                else:
                    try:
                        st.session_state["_exp_emb_cluster"] = client.export_embeddings(
                            work_dir=exp_wd,
                            mode="cluster",
                            cluster_id=str(ecid).strip(),
                        )
                    except Exception as e:
                        st.error(str(e))
            bc = st.session_state.get("_exp_emb_cluster")
            if bc:
                st.download_button(
                    "下载 JSON",
                    data=bc,
                    file_name=f"auto_tag_embeddings_cluster_{ecid}.json",
                    mime="application/json",
                    key="dl_exp_emb_cluster",
                )
        with tab_em_k:
            csz = st.number_input(
                "每块最多条数",
                min_value=1,
                max_value=_EXP_MAX,
                value=_EXP_MAX,
                key="exp_emb_chunk_sz",
            )
            nchunks = math.ceil(n_emb_tot / csz) if n_emb_tot > 0 and csz > 0 else 0
            st.caption(
                f"当前索引内约 **{n_emb_tot}** 条；每块 **{int(csz)}** 条时约 **{nchunks}** 块。"
            )
            if nchunks > max_btns:
                st.warning(f"块数超过 {max_btns}，请增大每块条数或改用 offset/limit。")
            if st.button("拉取索引各块", key="btn_exp_emb_chunk_prepare"):
                if nchunks > max_btns:
                    st.error("块数过多，已中止。")
                elif nchunks <= 0:
                    st.warning("无数据或请先刷新状态。")
                else:
                    chunks: List[tuple[int, bytes]] = []
                    err: Optional[str] = None
                    for idx in range(nchunks):
                        try:
                            chunks.append(
                                (
                                    idx,
                                    client.export_embeddings(
                                        work_dir=exp_wd,
                                        mode="chunk",
                                        chunk_index=idx,
                                        chunk_size=int(csz),
                                    ),
                                )
                            )
                        except Exception as e:
                            err = str(e)
                            break
                    if err:
                        st.error(err)
                    else:
                        st.session_state["_exp_emb_chunks"] = chunks
                        st.success(f"已就绪 {len(chunks)} 个文件。")
            for item in st.session_state.get("_exp_emb_chunks") or []:
                idx, blob = item[0], item[1]
                st.download_button(
                    f"下载 embeddings_chunk_{idx}.json",
                    data=blob,
                    file_name=f"auto_tag_embeddings_chunk_{idx}.json",
                    mime="application/json",
                    key=f"dl_exp_emb_chunk_{idx}",
                )

    with tab_db_dup:
        tab_dup_r, tab_dup_k = st.tabs(["按 offset/limit", "分块"])

        with tab_dup_r:
            d_off = st.number_input(
                "offset", min_value=0, value=0, key="exp_dup_range_off"
            )
            d_lim = st.number_input(
                "limit",
                min_value=1,
                max_value=_EXP_MAX,
                value=_EXP_MAX,
                key="exp_dup_range_lim",
            )
            if st.button("请求此段（仅侧车）", key="btn_exp_dup_range"):
                try:
                    st.session_state["_exp_dup_range"] = client.export_duplicates(
                        work_dir=exp_wd,
                        mode="range",
                        offset=int(d_off),
                        limit=int(d_lim),
                    )
                except Exception as e:
                    st.error(str(e))
            dr = st.session_state.get("_exp_dup_range")
            if dr:
                st.download_button(
                    "下载 JSON",
                    data=dr,
                    file_name=f"auto_tag_duplicates_range_{d_off}_{d_lim}.json",
                    mime="application/json",
                    key="dl_exp_dup_range",
                )
        with tab_dup_k:
            dcsz = st.number_input(
                "每块最多条数",
                min_value=1,
                max_value=_EXP_MAX,
                value=_EXP_MAX,
                key="exp_dup_chunk_sz",
            )
            ndchunks = math.ceil(dup_tot / dcsz) if dup_tot > 0 and dcsz > 0 else 0
            st.caption(
                f"侧车总计约 **{dup_tot}** 条；每块 **{int(dcsz)}** 条时约 **{ndchunks}** 块。"
            )
            if ndchunks > max_btns:
                st.warning(f"块数超过 {max_btns}，请增大每块条数。")
            if st.button("拉取侧车各块", key="btn_exp_dup_chunk_prepare"):
                if ndchunks > max_btns:
                    st.error("块数过多，已中止。")
                elif ndchunks <= 0:
                    st.warning("无数据或请先刷新状态。")
                else:
                    dchunks: List[tuple[int, bytes]] = []
                    err2: Optional[str] = None
                    for idx in range(ndchunks):
                        try:
                            dchunks.append(
                                (
                                    idx,
                                    client.export_duplicates(
                                        work_dir=exp_wd,
                                        mode="chunk",
                                        chunk_index=idx,
                                        chunk_size=int(dcsz),
                                    ),
                                )
                            )
                        except Exception as e:
                            err2 = str(e)
                            break
                    if err2:
                        st.error(err2)
                    else:
                        st.session_state["_exp_dup_chunks"] = dchunks
                        st.success(f"已就绪 {len(dchunks)} 个文件。")
            for item in st.session_state.get("_exp_dup_chunks") or []:
                idx, blob = item[0], item[1]
                st.download_button(
                    f"下载 duplicates_chunk_{idx}.json",
                    data=blob,
                    file_name=f"auto_tag_duplicates_chunk_{idx}.json",
                    mime="application/json",
                    key=f"dl_exp_dup_chunk_{idx}",
                )

    _db_subsection_title("标注")
    tab_cmp_s, tab_cmp_r, tab_cmp_k = st.tabs(
        ["共享字典", "offset/limit", "分块"]
    )

    with tab_cmp_s:
        st.caption(
            "平行数组中的 **labels_id / prefix_id / cluster_id** 需与此处的 **labels / prefix / cluster** 对照解码；"
            "建议先下载本文件，再下载 offset 或分块切片。"
        )
        if st.button("请求共享字典", key="btn_exp_cmp_shared"):
            try:
                st.session_state["_exp_cmp_shared"] = client.export_compact_shared(
                    work_dir=exp_wd
                )
            except Exception as e:
                st.error(str(e))
        csh = st.session_state.get("_exp_cmp_shared")
        if csh:
            st.download_button(
                "下载 compact_shared.json",
                data=csh,
                file_name="auto_tag_compact_labels_shared.json",
                mime="application/json",
                key="dl_exp_cmp_shared",
            )

    with tab_cmp_r:
        cmp_off = st.number_input(
            "offset", min_value=0, value=0, key="exp_cmp_range_off"
        )
        cmp_lim = st.number_input(
            "limit",
            min_value=1,
            max_value=_EXP_MAX,
            value=_EXP_MAX,
            key="exp_cmp_range_lim",
        )
        if st.button("请求平行字段切片", key="btn_exp_cmp_range"):
            try:
                st.session_state["_exp_cmp_range"] = client.export_compact_slice(
                    work_dir=exp_wd,
                    offset=int(cmp_off),
                    limit=int(cmp_lim),
                )
            except Exception as e:
                st.error(str(e))
        crr = st.session_state.get("_exp_cmp_range")
        if crr:
            st.download_button(
                "下载 compact_slice.json",
                data=crr,
                file_name=f"auto_tag_compact_slice_{cmp_off}_{cmp_lim}.json",
                mime="application/json",
                key="dl_exp_cmp_range",
            )

    with tab_cmp_k:
        ccsz = st.number_input(
            "每块最多行数",
            min_value=1,
            max_value=_EXP_MAX,
            value=_EXP_MAX,
            key="exp_cmp_chunk_sz",
        )
        n_cmp_est = n_emb_tot + dup_tot
        ncchunks = (
            math.ceil(n_cmp_est / ccsz) if n_cmp_est > 0 and ccsz > 0 else 0
        )
        st.caption(
            f"行数上界约 **{n_cmp_est}**（索引 {n_emb_tot} + 侧车 {dup_tot}，实际总行数以后端 export_meta 为准）；"
            f"每块 **{int(ccsz)}** 行时约 **{ncchunks}** 块。"
        )
        if ncchunks > max_btns:
            st.warning(f"块数超过 {max_btns}，请增大每块行数或改用 offset/limit。")
        if st.button("拉取各块（仅平行字段）", key="btn_exp_cmp_chunk_prepare"):
            if ncchunks > max_btns:
                st.error("块数过多，已中止。")
            elif ncchunks <= 0:
                st.warning("无数据或请先刷新状态。")
            else:
                cchunks: List[tuple[int, bytes]] = []
                err3: Optional[str] = None
                for idx in range(ncchunks):
                    try:
                        cchunks.append(
                            (
                                idx,
                                client.export_compact_chunk(
                                    work_dir=exp_wd,
                                    chunk_index=idx,
                                    chunk_size=int(ccsz),
                                ),
                            )
                        )
                    except Exception as e:
                        err3 = str(e)
                        break
                if err3:
                    st.error(err3)
                else:
                    st.session_state["_exp_cmp_chunks"] = cchunks
                    st.success(f"已就绪 {len(cchunks)} 个文件。")
        for item in st.session_state.get("_exp_cmp_chunks") or []:
            idx, blob = item[0], item[1]
            st.download_button(
                f"下载 compact_chunk_{idx}.json",
                data=blob,
                file_name=f"auto_tag_compact_chunk_{idx}.json",
                mime="application/json",
                key=f"dl_exp_cmp_chunk_{idx}",
            )


def _render_records_tab(client: AutoTagApiClient) -> None:
    st.caption(
        "分页查询 **work_dir** 下向量索引中的记录。留空覆盖项则用「设置」中的 work_dir。"
    )
    st.text_input("覆盖 work_dir（可选，与设置不一致时填写）", key="rec_work_dir")

    c1, c2 = st.columns(2)
    with c1:
        st.number_input("offset", min_value=0, key="rec_offset")
    with c2:
        st.number_input("limit", min_value=1, max_value=500, key="rec_limit")
    st.text_input("cluster_id 过滤（可选）", key="rec_cid")

    if st.button("查询 records", key="btn_query_records"):
        try:
            rwd = str(st.session_state.rec_work_dir).strip()
            gwd = _resolved_work_dir_str()
            wd_for_query = rwd or gwd or None
            data = client.list_records(
                offset=int(st.session_state.rec_offset),
                limit=int(st.session_state.rec_limit),
                cluster_id=(st.session_state.rec_cid or "").strip() or None,
                work_dir=wd_for_query,
            )
            _st_json_sorted(data)
        except Exception as e:
            st.error(str(e))


def _render_dup_tab(client: AutoTagApiClient) -> None:
    st.caption("使用上方「覆盖 work_dir」或「设置」中的全局 work_dir。")
    if st.button("加载近重复侧车记录", key="btn_load_dup"):
        try:
            dwd = str(st.session_state.rec_work_dir).strip()
            gwd = _resolved_work_dir_str()
            wd = dwd or gwd
            _st_json_sorted(client.list_duplicates(work_dir=wd, limit=200))
        except Exception as e:
            st.error(str(e))


def _render_other_page(client: AutoTagApiClient) -> None:
    st.subheader("健康检查")
    if st.button("检查 API", key="btn_health_other"):
        try:
            _st_json_sorted(client.health())
        except Exception as e:
            st.error(str(e))
    st.divider()
    st.subheader("关于")
    st.markdown(f"联系作者：[{AUTHOR_EMAIL}](mailto:{AUTHOR_EMAIL})")
    st.markdown(f"软件版本：**{APP_VERSION}**")


def _render_settings_page() -> None:
    st.caption(
        "默认工作目录为包内 `auto_tag/work_dir`，默认可编辑配置文件为 `auto_tag/config.json`；保存配置后若需后端重新加载，请重启后端进程。"
    )
    app = _app_settings()

    # 临时 ui_* key 仅用于控件；持久值在 app_settings，切页时 Streamlit 会删 ui_*，不会丢 app
    if "ui_work_dir" not in st.session_state:
        st.session_state.ui_work_dir = app["work_dir"]
    st.text_input(
        "全局 work_dir",
        key="ui_work_dir",
        help="可填绝对路径或相对路径；下方显示解析后的绝对路径。默认对应包内目录 auto_tag/work_dir。",
    )
    _flush_settings_ui_to_app_if_present()
    try:
        st.caption(f"**绝对路径（实际使用）**：`{_resolved_work_dir_str()}`")
    except Exception as e:
        st.caption(f"路径解析失败：{e}")

    st.divider()

    if "ui_config_path" not in st.session_state:
        st.session_state.ui_config_path = app["config_path"]
    st.text_input(
        "config.json 路径",
        key="ui_config_path",
        help="可填绝对路径或相对路径；下方显示解析后的绝对路径。默认对应 auto_tag/config.json。",
    )
    _flush_settings_ui_to_app_if_present()
    try:
        st.caption(f"**绝对路径（读写目标）**：`{_resolved_config_path()}`")
    except Exception as e:
        st.caption(f"路径解析失败：{e}")

    # 须在 st.text_area(key=ui_config_text) 之前写入磁盘内容到 app，否则同一次 run 内改 key 会触发异常
    if st.session_state.pop("_cfg_reload_on_next_run", False):
        _reload_config_text_from_disk()
        st.session_state.pop("ui_config_text", None)
    elif app.get("config_text") is None:
        _reload_config_text_from_disk()

    if "ui_config_text" not in st.session_state:
        st.session_state.ui_config_text = app["config_text"] or "{\n}\n"

    st.text_area(
        "config.json 内容",
        key="ui_config_text",
        height=420,
        help="结构与所选路径下 config.json 一致，例如 batch_size、tau_dup、questions 等",
    )

    _flush_settings_ui_to_app_if_present()

    c1, c2 = st.columns(2)
    with c1:
        if st.button("从磁盘重新加载", key="btn_cfg_reload"):
            st.session_state._cfg_reload_on_next_run = True
            st.rerun()
    with c2:
        if st.button("保存到磁盘", key="btn_cfg_save", type="primary"):
            try:
                _flush_settings_ui_to_app_if_present()
                text = str(_app_settings()["config_text"])
                json.loads(text)
                out_path = _resolved_config_path()
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(text, encoding="utf-8")
                st.success(f"已写入：`{out_path}`")
            except json.JSONDecodeError as e:
                st.error(f"JSON 无效: {e}")
            except Exception as e:
                st.error(str(e))

    st.divider()
    if "ui_env_path" not in st.session_state:
        st.session_state.ui_env_path = app["env_path"]
    st.text_input(
        ".env 路径",
        key="ui_env_path",
        help="默认 auto_tag/.env；保存后需重启后端进程方可使新环境变量生效。",
    )
    _flush_settings_ui_to_app_if_present()
    try:
        st.caption(f"**绝对路径**：`{_resolved_env_path()}`")
    except Exception as e:
        st.caption(f"路径解析失败：{e}")

    if st.session_state.pop("_env_reload_on_next_run", False):
        _reload_env_text_from_disk()
        st.session_state.pop("ui_env_text", None)
    elif app.get("env_text") is None:
        _reload_env_text_from_disk()

    if "ui_env_text" not in st.session_state:
        st.session_state.ui_env_text = app["env_text"] or ""

    st.text_area(
        ".env 内容",
        key="ui_env_text",
        height=200,
        help="KEY=value 形式；与 pydantic-settings 读取的 .env 一致。",
    )
    _flush_settings_ui_to_app_if_present()

    e1, e2 = st.columns(2)
    with e1:
        if st.button("从磁盘重新加载 .env", key="btn_env_reload"):
            st.session_state._env_reload_on_next_run = True
            st.rerun()
    with e2:
        if st.button("保存 .env 到磁盘", key="btn_env_save", type="primary"):
            try:
                _flush_settings_ui_to_app_if_present()
                out_env = _resolved_env_path()
                out_env.parent.mkdir(parents=True, exist_ok=True)
                out_env.write_text(str(_app_settings()["env_text"]), encoding="utf-8")
                st.success(f"已写入：`{out_env}`（请重启后端使环境变量生效）")
            except Exception as e:
                st.error(str(e))


if __name__ == "__main__":
    main()
