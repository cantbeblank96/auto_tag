# Release Record

## v0.0.4 (2026-07-15)

Windows 一键启停、跨平台重启日志、`skip_if_in_db` 侧车跳过、设置页路径解析与数据库「高级」清空的维护版本。

### Added

- **一键启停脚本** — Windows：`scripts/windows/start_web|stop_web|restart_web(.bat/.ps1)`；Linux/macOS：`scripts/linux/start_web|stop_web|restart_web.sh`（后台启动前后端，日志写入仓库 `logs/`）。
- **数据库高级操作** — `POST /api/database/clear_embeddings`、`clear_duplicates`（需 `confirm=true`）；前端「数据库 → 高级」确认后清空向量索引或近重复侧车。
- **项目路径 API** — `GET /api/utils/paths`；设置页用后端实际路径解析 `{PROJECT_PATH}`，修复 Windows 上硬编码 Linux 根路径导致读配置 403 / 模型列表为空。

### Changed

- Version bumped to `0.0.4`（`auto_tag/constant.py`、`pyproject.toml`）。
- **后端重启日志** — 默认改为仓库根目录 `logs/auto_tag_backend.log`（不再依赖 `/tmp` 或易被占用的系统 TEMP）；Windows 重启 API 不再预开日志文件，避免「Permission denied」。
- **README** — 补充 Windows 非技术向启停说明与 Linux 一键脚本用法。

### Fixed

- **`skip_if_in_db` 重复处理近重复图** — Stage1 近重复默认不入向量库，此前仅跳过 Chroma 路径；现同时跳过侧车已登记路径，并在侧车 append 时去重。
- **任务页误报失败** — 排队提交不再在 `setState` updater 内调用 `createJob`；增加 in-flight 护栏，避免 React Strict Mode 双提交导致 409 busy。
- **Windows 前端启动参数** — `run_web_frontend_v2.ps1` 直接调用 `vite.js`，避免 `npm` 吞掉 `--host` / `--port`。
- **Windows 后端重启脚本** — 修正 stdout/stderr 同文件重定向问题；用 wmic/cmd 脱离会话拉起新进程。

---

## v0.0.3 (2026-07-14)

CLIP 建簇与 VLM 打标解耦、VLM 并发/超时可配、任务耗时与模型调用统计持久化的迭代版本。

### Added

- **CLIP / VLM 解耦** — `cluster_engine.py` 只做双阈值建簇；`vlm_annotation_pool.py` 全局 worker 池异步打标并回写中心标签；`annotator.py` 降为门面。
- **VLM 对话式改正** — 校验失败时可多轮改正（`vlm_validation_max_corrections`）；空 content 走 `EmptyVLMResponseError`，避免无效改正轮。
- **流水线 Debug 时序** — `pipeline_debug=true`（或 `AUTO_TAG_VLM_TIMING=1`）时写入 `work_dir/log/`：`vlm_timing.json` / `.png`、`vlm_http_trace.txt`、`vlm_timing_summary.json`、`pipeline_profile.json`。
- **辅助脚本** — `auto_tag/scripts/`：`plot_vlm_timing.py`、`print_vlm_http_trace.py`、`capture_vlm_http_trace.py`、`bench_vlm_timing_compare.py`。
- **模型调用统计持久化** — 任务结束后写入 `work_dir/log/vlm_endpoint_stats.json`；后端启动时恢复；设置页按端点展示「调用 / 失败」次数并定时刷新。
- **任务耗时** — 任务记录 `started_at` / `finished_at`；任务列表与查询表增加「耗时」列（运行中每秒刷新）。

### Changed

- Version bumped to `0.0.3`（`auto_tag/constant.py`、`pyproject.toml`）。
- **VLM HTTP** — 可配置 `vlm_http_timeout`（默认 60s）；`vlm_concurrency` 控制打标池并发。
- **进度语义** — `processed` 表示建簇进度；`vlm_calls` / `new_centers` 表示 VLM 完成 / 待标簇中心。
- **config.example.json** — 增加 `vlm_concurrency`、`vlm_http_timeout`、`vlm_validation_max_corrections`、`pipeline_debug`；默认策略示例改为 `round_robin`。
- **架构文档** — `notes/for_developer/ARCHITECTURE.md` 同步生产者–消费者模型。

### Fixed

- **设置页调用次数常为 0** — 此前熔断统计仅在内存中，CLI 跑任务或重启后端后丢失；现已持久化并在 API / 前端合并展示。
- **批内近邻不可见** — 同批尚未入库的簇中心通过批内内存索引参与 Stage1/2 距离判定，避免重复建簇。

### Configuration

新增 / 强化键（详见 `auto_tag/config.example.json`）：

| 键 | 默认 | 说明 |
|----|------|------|
| `vlm_concurrency` | `10` | VLM 池并发 worker 数 |
| `vlm_http_timeout` | `60` | 单次 HTTP 读超时（秒） |
| `vlm_validation_max_corrections` | `2` | 校验失败改正轮数（0=仅首轮） |
| `pipeline_debug` | `false` | 写出 VLM 时序图与 profile |

本地导出目录请使用仓库根目录 `temp/`（已在 `.gitignore`）；勿提交含真实密钥的 `config.json`。

---

## v0.0.2 (2026-07-02)

Web 控制台体验、版本管理、uv 环境与 VLM 端点区分的迭代版本。

### Added

- **统一版本号** — `auto_tag/constant.py` 中的 `VERSION` 为单一数据源；后端 health/API 与 Vite 前端（`__APP_VERSION__`）构建时同步读取。
- **首页整合** — 侧栏点击「Auto Tag」进入首页，合并原「教程」与「关于」：可折叠使用教程 + 服务状态（健康检查、**重启后端**）。
- **后端重启 API** — `POST /api/utils/restart_backend` 通过 `scripts/linux|windows/restart_web_backend.*` 独立进程拉起新后端，避免在请求线程内 spawn 失败；`GET /api/utils/backend_status` 供重启前确认进行中任务。
- **配置热加载** — `POST /api/utils/reload_config` 在不重启进程时从磁盘重载 `config.json`（进行中的任务仍可能使用旧参数）。
- **VLM 端点 id** — 每个 `vlm_models` 条目支持配置字段 **`id`**（UUID）；熔断器、连通性测试、重置熔断按 **`endpoint_id`** 区分，同名 `name` 的多条配置不再共用状态（`vlm_model_utils.py`）。
- **uv 项目化** — 根目录 `pyproject.toml` + `uv.lock`；`scripts/linux/`、`scripts/windows/` 提供 `test_uv`、`setup_uv_env`、`run_web_*`、`restart_web_backend` 等脚本。
- **版本发布记录** — 本文件 `notes/Release_Record.md`。

### Changed

- Version bumped to `0.0.2`（`auto_tag/constant.py`、`pyproject.toml`）。
- **依赖管理** — 移除 `auto_tag/requirements.txt` 与 `auto_tag/requirements-web.txt`；安装改为 `uv sync --extra web` 或 `bash scripts/linux/setup_uv_env.sh`。
- **开发环境** — 文档与脚本从 conda `agent_d` 迁移至仓库根 `.venv`（uv）。
- **前端端口** — Vite 开发服务器固定 **5020**（`/api` 代理至后端 **8000**）。
- **侧栏顺序** — 任务 → 数据库 → 图片查询 → 设置；无独立「首页 / 教程 / 关于」菜单项（`/tutorial`、`/about` 重定向至 `/`）。
- **任务页** — 任务历史并入「任务」页；「查询」章节默认折叠。
- **数据库页** — 「更新」区域内「查看 JSON」默认折叠。

### Fixed

- **任务历史持久化** — `job_store` 正确解析 `web_job_history.json` 中 `jobs` 为列表的格式，重启后端后历史不丢失。
- **后端重启** — 使用独立重启脚本 + `start_new_session`，修复原先在 daemon 线程中拉起新进程被 `os._exit` 一并终止的问题。

### Configuration

- 建议在 `vlm_models` 每条目中显式配置 **`id`**（UUID），尤其当多条共用相同 `name` 时。保存设置时可为缺省条目自动生成 id。
- 修改 `config.json` 后可通过首页「重启后端」或 API `reload_config` 使内存配置与磁盘一致。

---

## v0.0.1 (2026-06-28)

从 `kevin_agent` 抽取的初始发布。基于 **CLIP** 特征 + **ChromaDB** 向量检索 + **VLM** 的批量图像自动标注系统。

### Features

- **双阈值流水线** — `tau_dup` 近重复跳过（侧车登记）、`tau_cls` 簇内继承标签、超阈值新簇触发 VLM 打标。
- **CLIP 批处理** — GPU 批量特征提取与 L2 归一化；cosine 空间 ChromaDB 索引。
- **VLM 多模型** — 优先级 Failover 与 Round-Robin；熔断器与指数退避重试。
- **增量持久化** — `work_dir` 下向量索引、构建快照、近重复侧车（SQLite/JSONL）、路径前缀压缩。
- **CLI** — `python -m auto_tag.main`、`python -m auto_tag.view_db`、紧凑标注导出。
- **FastAPI 后端** — 标注任务、记录查询、数据库维护与导出、VLM/熔断配置。
- **React v2 前端** — Vite + Tailwind + React Query；任务、图片查询、数据库、设置等页面。
- **YUV 支持** — NV21/NV12/YUV420p 与混合目录模式；旋转与样图校验。
- **Streamlit 旧版前端** — 保留在 `auto_tag/frontend_streamlit/`（可选）。

### Tech Stack

| Layer | Choice |
|-------|--------|
| Core | Python 3.11+, PyTorch, CLIP, ChromaDB, Pydantic |
| Backend | FastAPI, uvicorn |
| Frontend | React + TypeScript + Vite + Tailwind |
| CLI / 批处理 | `auto_tag.main`, `core/pipeline.py` |

### Configuration

- 运行时配置：`auto_tag/config.json`（含 `tau_dup`、`tau_cls`、`vlm_models`、`questions` 等）。
- 工作目录：`work_dir/embedding_index`（或兼容旧版 `chroma_data`）、`work_dir/log/`。
