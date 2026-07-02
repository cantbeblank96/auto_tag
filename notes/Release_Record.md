# Release Record

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
