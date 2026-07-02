# auto_tag — 开发者手册

项目定位：基于 **CLIP 特征** + **ChromaDB 向量检索** + **VLM 多模态大模型** 的批量图像自动标注系统。

## 文档索引

| 文档 | 适用读者 | 内容 |
|---|---|---|
| **本文件 (README.md)** | 所有开发者 | 项目概览、启动方式、目录结构、核心原理 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 架构师 / 新功能开发者 | 模块依赖图、API 路由图、前端组件关系、数据流向 |
| [DATABASE_UPDATE_BOX.md](./DATABASE_UPDATE_BOX.md) | 维护功能 / 测试 | 数据库页「更新」区域：快照比对、三种维护操作、按钮启用条件 |
| [DATABASE_EXPORT_BOX.md](./DATABASE_EXPORT_BOX.md) | 数据导出 / 测试 | 数据库页「导出」区域：三类数据源、range/chunk/cluster、紧凑标注两步下载 |
| [EXTENDING.md](./EXTENDING.md) | 功能拓展开发者 | 各模块扩展点、新增 VLM 后端、新增路由、定制标注逻辑 |
| [../Release_Record.md](../Release_Record.md) | 所有人 | 版本发布记录 |

---

## 一句话原理

```
CLIP 提特征 → ChromaDB 查最近邻 → 双阈值判定：
  dist ≤ tau_dup  → 近重复，跳过（侧车登记）
  tau_dup < dist ≤ tau_cls → 同簇，继承标签
  dist > tau_cls  → 新簇，调 VLM 打标入库
```

---

## 快速启动

在**仓库根目录**执行（依赖由 [uv](https://docs.astral.sh/uv/) 管理，见 `pyproject.toml` / `uv.lock`）：

```bash
bash scripts/linux/setup_uv_env.sh   # 首次：创建 .venv 并 uv sync --extra web
source .venv/bin/activate
export PYTHONPATH=$PYTHONPATH:.

# 1. 后端 API（默认端口 8000）
bash scripts/linux/run_web_backend.sh

# 2. 前端 Vite（端口 5020，代理 /api → 8000）
bash scripts/linux/run_web_frontend_v2.sh

# 3. CLI 直接跑流水线
python -m auto_tag.main --input_dir /path/to/images --work_dir ./my_work

# 4. 查看已有索引
python -m auto_tag.view_db
python -m auto_tag.view_db --output_path out.json
```

Windows 使用 `scripts/windows/` 下同名 `.ps1` 脚本。版本号单一来源：`auto_tag/constant.py`（当前见 [Release_Record.md](../Release_Record.md)）。

> 端口说明：后端 **8000**；Vite 开发服务器 **5020**（`auto_tag/web/vite.config.ts`）。

---

## 目录结构速览

```
kevin_auto_tag/                 # 仓库根
├── pyproject.toml / uv.lock    # uv 依赖定义与锁文件
├── scripts/linux/              # bash：uv、Web 启动、重启后端
├── scripts/windows/            # PowerShell 对应脚本
├── notes/                      # 本文档目录
│
└── auto_tag/
    ├── constant.py             # VERSION（前后端共用）
    ├── main.py                 # CLI 入口
    ├── view_db.py              # ChromaDB 查看工具
    ├── config.json             # 运行时配置（.gitignore）
    │
    ├── core/                   # ★ 核心业务逻辑
    │   ├── config.py           #   Pydantic Settings + 配置加载
    │   ├── feature_extractor.py
    │   ├── vector_db.py
    │   ├── annotator.py        #   ★ 双阈值聚类标注引擎
    │   ├── vlm_client.py
    │   ├── vlm_model_utils.py  #   VLM 端点 id（熔断/测试区分）
    │   ├── pipeline.py
    │   ├── duplicate_store.py
    │   ├── database_maintenance.py
    │   ├── db_build_snapshot.py
    │   ├── circuit_breaker.py
    │   └── utils/
    │
    ├── backend/                # FastAPI
    │   ├── app.py
    │   ├── job_runner.py
    │   ├── job_store.py        #   Web 任务历史持久化
    │   └── routers/
    │
    ├── web/                    # React 前端 (Vite + Tailwind)
    │   └── src/
    │       ├── App.tsx         #   路由（首页 / 任务 / 数据库 / 图片查询 / 设置）
    │       ├── pages/Home.tsx  #   教程 + 系统信息（健康检查、重启后端）
    │       ├── pages/Tasks.tsx #   标注任务 + 历史查询（「查询」默认折叠）
    │       ├── components/TutorialContent.tsx
    │       └── components/SystemInfoSection.tsx
    │
    ├── frontend_streamlit/     # 旧版 Streamlit（可选）
    └── test/
```

`core/` 内还包括 `path_prefix_registry.py`、`compact_labels_export.py`、`config_file_params.py` 等；`backend/routers/` 含 `health`（含重启后端）、`jobs`、`models`、`records`、`duplicates`、`database`。

---

## 核心数据结构

### ChromaDB 文档元数据

```python
# 每个 Chroma 文档存储如下 metadata dict：
{
    "image_path": "/abs/path/to/image.jpg",   # 旧格式（无 registry 时）
    "path_prefix_id": "p0",                    # 新格式：前缀 ID
    "image_rel_path": "subdir/image.jpg",      # 新格式：相对路径
    "cluster_id": "cls_a1b2c3d4",             # 簇标识
    "is_cluster_center": True,                # 是否簇中心
    "labels_json": '{"scene":"...", ...}',     # VLM 输出（JSON 字符串）
    "media_kind": "rgb",                      # rgb / yuv
    "pix_w": 1920, "pix_h": 1080,
    "yuv_layout": "nv21",
}
```

### 构建快照 (`work_dir/log/auto_tag_db_build_snapshot.json`)

每条流水线成功结束后写入，记录构建时的 `tau_dup`, `tau_cls`, `questions`, `vlm_models` 等。`/api/database/stats` 用其与当前 config 做差异比对，出现差异时前端亮黄牌提醒。

---

## 关键约束 / 约定

| 约定 | 说明 |
|---|---|
| **work_dir 统一由后端 config 管理** | 所有运行时数据写入 `work_dir/` 下。后端各路由不传 work_dir 时自动回退到 `settings.db_path` 反向推导。 |
| **线程互斥** | `job_runner.py` 使用 `threading.Lock` 保证同一时刻只有一个任务或维护操作在执行。数据库维护（重算/重建/重标注）通过 `run_exclusive_task` 共用同一互斥锁。 |
| **路径压缩** | ChromaDB 中优先使用 `path_prefix_id + image_rel_path` 而非完整绝对路径，通过 `PathPrefixRegistry` 双向映射。 |
| **cosine 距离** | 向量空间为 cosine，ChromaDB 初始化时指定 `hnsw:space=cosine`，特征提取后做 L2 归一化。 |
| **GPU 批处理** | CLIP 推理使用 `torch.no_grad()` + batch 处理，建议 batch_size=32。 |
| **config.json 被 gitignore** | 含 API Key 的 `config.json` 被 `.gitignore` 排除，`.env` 同理。 |
| **VLM 熔断器** | 按 **`endpoint_id`**（配置字段 `id` 或自动生成）区分各端点，同名 `name` 的多条配置独立计数与熔断。 |
| **双独立导出端点** | embedding 和 duplicate 的导出分为 `/export_embeddings` 和 `/export_duplicates` 两个独立路由，默认 limit 上限 200000。 |
| **紧凑导出三端点** | 分 `export_compact_shared`（字典）、`export_compact_slice`（偏移）、`export_compact_chunk`（分块）三种。 |