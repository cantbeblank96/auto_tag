# auto_tag 实现说明与架构笔记

本文档汇总当前仓库中与「双阈值标注 + Stage 1 侧车 + HTTP/Web」相关的实现与运行方式，便于后续迭代。

## 1. 目录结构（均在 `auto_tag/` 下）

| 路径 | 职责 |
|------|------|
| [auto_tag/](../) 根目录 | 入口：`main.py`、`view_db.py`；文档与 `config.json` |
| [core/](../core/) | 核心算法与 IO：`annotator`、`vector_db`、`vlm_client`、`feature_extractor`、`pipeline`、`duplicate_store`、`utils/load_image` |
| [backend/](../backend/) | FastAPI：`uvicorn auto_tag.backend.app:app`，前缀 `/api` |
| [frontend_streamlit/](../frontend_streamlit/) | Streamlit UI，仅通过 `httpx` 调后端 |
| [requirements-web.txt](../requirements-web.txt) | Web 栈额外依赖（与 `requirements.txt` 叠加） |

**原则**：业务核心在 **`auto_tag/core/`**；根目录仅保留 CLI、查看器与配置文件；`backend/`、`frontend_streamlit/` 为 Web 层。

## 2. Stage 1 冗余记录（方案 B）

- 当 `d <= tau_dup` 时不写入 Chroma，但若 `settings.record_stage1_duplicates` 为 true，则向 **`log_dir/duplicate_links.jsonl`** 追加一行 JSON。
- 字段：`anchor_id`（近邻 Chroma 文档 id）、`anchor_path`、`dup_path`、`distance`、`ts`（UTC ISO）。
- [core/vector_db.py](../core/vector_db.py) 的 `query_batch` 返回 `(distances, metadatas, ids)`，`ids` 用于 `anchor_id`。
- 配置：[config.json](../config.json) 中 `record_stage1_duplicates`、`duplicate_links_filename`。

## 3. 流水线与 CLI

- [core/pipeline.py](../core/pipeline.py)：`collect_image_paths`、`save_verify_samples`、`run_annotation_pipeline`。
- [main.py](../main.py)：配置日志 → 可选交互式样图确认 → `run_annotation_pipeline`。
- **混合目录**：`--b_mixed_yuv` 时，`.nv21` / `.nv12` / `.yuv` 按 YUV 解码，其余按普通图；需正确传入 `--image_width` / `--image_height`。
- **递归扫描**：`input_dir` 下使用 `os.walk` 收集后缀匹配文件，支持多层子目录（如试采 `Front/场景子文件夹/*.jpg`）。
- 扫描后缀见 `pipeline.DEFAULT_IMAGE_SUFFIXES`（含 `.webp`）。

## 4. 后端 API（FastAPI）

- `GET /api/health`
- `POST /api/jobs`：body 与 `JobCreate` 一致（见 [backend/routers/jobs.py](../backend/routers/jobs.py)）
- `GET /api/jobs/{job_id}`、`GET /api/jobs/{job_id}/logs?tail=200`
- `GET /api/records?offset=&limit=&cluster_id=`
- `GET /api/duplicates?log_dir=&offset=&limit=`

**并发**：全局同一时刻仅允许一个运行中的任务（见 [job_runner.py](../backend/job_runner.py)）。

## 5. Streamlit 前端

```bash
export PYTHONPATH=/path/to/kevin_agent:$PYTHONPATH
streamlit run auto_tag/frontend_streamlit/app.py
```

或（在仓库根目录）：

```bash
bash auto_tag/scripts/run_web_frontend.sh
```

环境变量 `AUTO_TAG_API_BASE` 可设 API 根地址（默认 `http://127.0.0.1:8000`）。

## 6. 启动后端

```bash
cd /path/to/kevin_agent
export PYTHONPATH=$PWD:$PYTHONPATH
uvicorn auto_tag.backend.app:app --host 0.0.0.0 --port 8000
```

或：

```bash
bash auto_tag/scripts/run_web_backend.sh
```

## 6.1 `auto_tag/scripts/` 脚本

脚本会优先使用 **`$HOME/anaconda3/envs/agent_d/bin/python`**（若存在），与仓库约定一致；也可设置 **`PYTHON_EXECUTABLE`** 覆盖。

| 脚本 | 说明 |
|------|------|
| `_conda_agent_d.sh` | 被其它脚本 `source`，解析 `PYTHON_EXEC` |
| `run_trial_front.sh` | 试采 Front 目录 CLI（与 §7 参数一致）；可设 `INPUT_DIR`、`LOG_DIR` |
| `run_web_backend.sh` | FastAPI；可设 `HOST`、`PORT` |
| `run_web_frontend.sh` | Streamlit；可设 `AUTO_TAG_API_BASE` |

## 7. 试采数据目录测试示例

目录：`/SDA/data_aqu_2024-12-26/试采/Pictures/Front`（含 JPG 与 NV21，NV21 为 640×480，**逆时针 90°** 即 `ROTATE_90_COUNTERCLOCKWISE`）。

**CLI 示例**（跳过交互确认、使用独立 log/chroma 以免污染默认库），等价于执行 `bash auto_tag/scripts/run_trial_front.sh`：

```bash
export PYTHONPATH=$PWD:$PYTHONPATH
python -m auto_tag.main \
  --input_dir "/SDA/data_aqu_2024-12-26/试采/Pictures/Front" \
  --log_dir "./run_logs_front" \
  --b_skip_image_manually_verified \
  --b_mixed_yuv \
  --image_width 640 \
  --image_height 480 \
  --rotate_angle ROTATE_90_COUNTERCLOCKWISE
```

> 说明：完整跑通依赖本机 CUDA/CLIP、Chroma、VLM（API 或本地模型）。若 VLM API 失效需更换密钥或模型名（`.env` 中 `VLM_MODEL_NAME` / `VLM_API_KEY`）。

## 8. 已知限制（MVP）

- `GET /api/records` 在带 `cluster_id` 过滤时，`total` 字段为 `null`（Chroma 侧未做廉价 count）。
- `read_duplicate_links_jsonl` 为内存全量载入后分页，超大 JSONL 需后续换 SQLite。
- 缩略图与路径白名单未在 MVP 中实现，仅保留 `safe_path_check` 占位思路。

---

*文档随实现更新；原始设计讨论见仓库内历史计划文件。*
