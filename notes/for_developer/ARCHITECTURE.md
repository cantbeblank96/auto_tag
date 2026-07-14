# 技术架构

> 涵盖模块依赖、API 路由、前端组件、核心算法、数据流向。

---

## 1. 系统总览

```mermaid
graph TB
    subgraph Frontend ["前端 React (Vite + Tailwind)"]
        Layout["Layout.tsx<br/>侧边栏导航"]
        Home["Home.tsx<br/>教程 + 系统信息"]
        Tasks["Tasks.tsx<br/>标注任务 + 历史查询"]
        ImageQuery["ImageQuery.tsx<br/>图片查询"]
        Database_["Database.tsx<br/>数据库控制台"]
        Settings["Settings.tsx<br/>配置管理"]
        API_Client["api/client.ts<br/>API 客户端"]
    end

    subgraph Backend ["后端 FastAPI"]
        App["app.py<br/>FastAPI 应用"]
        JobRunner["job_runner.py<br/>后台任务管理器"]
        Routes["routers/<br/>6 组路由"]
    end

    subgraph Core ["核心引擎"]
        Pipeline["pipeline.py<br/>流水线编排"]
        Annotator["annotator.py<br/>CLIP/VLM 门面"]
        ClusterEngine["cluster_engine.py<br/>双阈值建簇（生产者）"]
        VlmPool["vlm_annotation_pool.py<br/>全局 VLM 池（消费者）"]
        Extractor["feature_extractor.py<br/>CLIP 特征提取"]
        VectorDB["vector_db.py<br/>ChromaDB 封装"]
        VLM["vlm_client.py<br/>VLM 多模型调用"]
        ImageLoadCtx["image_load_context.py<br/>解码参数共用"]
        Config["config.py<br/>配置管理"]
        CircuitBreaker["circuit_breaker.py<br/>熔断器"]
        DuplicateStore["duplicate_store.py<br/>近重复侧车"]
        PathRegistry["path_prefix_registry.py<br/>路径压缩"]
        Maintenance["database_maintenance.py<br/>重算/重建/重标注"]
        Snapshot["db_build_snapshot.py<br/>构建快照"]
        CompactExport["compact_labels_export.py<br/>紧凑导出"]
        ConfigParams["config_file_params.py<br/>Web 配置比对"]
    end

    subgraph Storage ["持久化"]
        ChromaDB["ChromaDB<br/>向量索引"]
        SQLite["work_dir/log/<br/>duplicate_links.sqlite"]
        JSON["work_dir/log/<br/>*.json"]
        ConfigFile["config.json"]
    end

    subgraph AI ["AI 模型"]
        CLIP["openai/clip-vit-base-patch32<br/>特征提取"]
        VLMModels["VLM 多模型<br/>（Gemini / GPT / 自定义）"]
    end

    Layout --> Home & Tasks & ImageQuery & Database_ & Settings
    Tasks & ImageQuery & Database_ & Settings & Home --> API_Client
    API_Client --> |HTTP /api/*| Routes

    Routes --> App
    Routes --> JobRunner
    JobRunner --> Pipeline
    Pipeline --> Annotator
    Annotator --> ClusterEngine
    Annotator --> VlmPool
    Annotator --> Extractor --> CLIP
    ClusterEngine --> VectorDB --> ChromaDB
    VlmPool --> VLM --> VLMModels
    VlmPool --> VectorDB
    VlmPool --> ImageLoadCtx
    ClusterEngine --> DuplicateStore --> SQLite
    Annotator --> PathRegistry --> JSON
    Pipeline --> Snapshot --> JSON
    Pipeline --> Config --> ConfigFile
    Core --> CircuitBreaker
    Routes --> Maintenance
    Maintenance --> VectorDB & VLM & DuplicateStore
    Routes --> CompactExport --> JSON
```

---

## 2. 核心算法：双阈值建簇 + 异步 VLM 标注

CLIP 建簇与 VLM 打标已**解耦**为生产者–消费者模型：`ClusterEngine` 只写向量与簇关系，新簇中心以空 `labels_json` 入库并异步入队；`VlmAnnotationPool` 在全局 worker 池中并行打标并回写中心、传播成员标签。

```mermaid
flowchart TB
    START(["输入图片列表"]) --> LOAD["分批加载<br/>batch_size=32"]
    LOAD --> POOL_START["启动 VlmAnnotationPool<br/>vlm_concurrency 个 worker"]
    POOL_START --> EXTRACT["CLIP 批量提特征<br/>extract_features_batch()"]

    EXTRACT --> BOOT{"数据库<br/>为空？"}
    BOOT -->|是| BOOT_INSERT["首图立刻建簇中心<br/>labels=空 · status=pending<br/>submit → VLM 队列"]
    BOOT_INSERT --> QUERY

    BOOT -->|否| QUERY["ChromaDB Top-1 最近邻<br/>query_batch()"]
    QUERY --> LOOP["逐图双阈值判定"]

    LOOP --> DUP{"dist ≤ tau_dup?"}
    DUP -->|Stage 1| SKIP["跳过入库 · 侧车登记"]
    SKIP --> NEXT

    DUP -->|否| CLUSTER{"dist ≤ tau_cls?"}
    CLUSTER -->|Stage 2| MERGE["继承 cluster_id/labels<br/>写入 ChromaDB（不调 VLM）"]
    MERGE --> NEXT

    CLUSTER -->|Stage 3| NEW["新簇中心立刻入库<br/>labels=空 · pending<br/>submit → VLM 队列"]
    NEW --> NEXT

    NEXT["下一张 / 下一批"] --> LOOP
    NEXT --> LOAD

    subgraph VLM_POOL ["VlmAnnotationPool（与建簇并行）"]
        Q["任务队列"] --> W["Worker × N"]
        W --> VLM_CALL["VLM annotate_image"]
        VLM_CALL --> UPDATE["更新中心 labels<br/>传播同簇成员"]
    end

    BOOT_INSERT --> Q
    NEW --> Q

    LOAD --> DRAIN["全部批次结束后<br/>wait_idle · shutdown pool"]
    DRAIN --> DONE(["PipelineResult"])
```

### 进度 API 字段（Web 双进度条）

| 字段 | 含义 |
|------|------|
| `processed` / `total` | **建簇阶段**：已完成双阈值判定的图片数 |
| `new_centers` | 已入队的待 VLM 标注簇中心数 |
| `vlm_calls` | 已完成 VLM 标注的簇中心数 |
| `stage1_skips` | Stage 1 近重复跳过 |
| `stage2_joins` | Stage 2 并入已有簇 |

建簇与 VLM 可**重叠**：`processed < total` 时 VLM 可能已在跑；`processed == total` 且 `vlm_calls < new_centers` 时为 VLM 收尾阶段。

**批内可见性**：同一 batch 内 Chroma 只查一次，故 `ClusterEngine` 维护本批内存索引——近重复（Stage1）对任意已处理向量比距；聚类（Stage2/3）仅对本批**簇中心**比距，避免 Stage2 成员误拉低距离。

### 阈值含义

| 参数 | 默认值 | 含义 |
|---|---|---|
| `tau_dup` | 0.05 | **近重复阈值**。cosine 距离 ≤ tau_dup 判定为重复/冗余帧，不入库 |
| `tau_cls` | 0.25 | **聚类阈值**。tau_dup < dist ≤ tau_cls 判定为同簇成员，继承簇标签 |
| `dist > tau_cls` | — | **新簇阈值**。立刻创建簇中心（labels 待标），异步入 VLM 队列 |

---

## 3. 模块依赖关系

```mermaid
graph LR
    %% 核心层依赖
    pipeline --> annotator
    pipeline --> image_load_context
    pipeline --> feature_extractor
    pipeline --> vector_db
    pipeline --> duplicate_store
    pipeline --> path_prefix_registry
    pipeline --> config

    annotator --> cluster_engine
    annotator --> vlm_annotation_pool
    annotator --> feature_extractor
    annotator --> vector_db
    annotator --> vlm_client
    annotator --> image_load_context

    cluster_engine --> vector_db
    cluster_engine --> duplicate_store
    cluster_engine --> path_prefix_registry
    cluster_engine --> vlm_annotation_pool

    vlm_annotation_pool --> vlm_client
    vlm_annotation_pool --> vector_db
    vlm_annotation_pool --> image_load_context

    database_maintenance --> annotator
    database_maintenance --> vector_db
    database_maintenance --> vlm_client
    database_maintenance --> duplicate_store
    database_maintenance --> db_build_snapshot

    vlm_client --> circuit_breaker
    compact_labels_export --> vector_db
    config_file_params --> config

    %% 后端依赖
    routers --> config
    routers --> pipeline
    routers --> job_runner
    routers --> database_maintenance
    routers --> duplicate_store
    routers --> compact_labels_export
    job_runner --> pipeline

    %% 依赖方向说明
    subgraph 依赖方向
        DIR["A --> B 表示 A 依赖 B"]
    end
```

---

## 4. API 路由总览

```mermaid
graph LR
    subgraph API["/api/* 路由组"]
        Health["health.py<br/>· GET /health<br/>· GET /utils/backend_status<br/>· POST /utils/restart_backend<br/>· POST /utils/reload_config<br/>· GET /utils/read_file<br/>· POST /utils/write_file"]
        Jobs["jobs.py<br/>· GET /jobs (列表含 server_started_at)<br/>· POST /jobs<br/>· GET /jobs/{id}<br/>· GET /jobs/{id}/logs"]
        Models["models.py<br/>· GET /models<br/>· GET /models/circuit-breaker<br/>· PUT /models/circuit-breaker<br/>· POST /models/test<br/>· POST /models/reset<br/>· POST /models/reset/{endpoint_id}"]
        Records["records.py<br/>· GET /records<br/>· GET /records/by_path<br/>· GET /records/preview<br/>· GET /records/safe_path_check<br/>· POST /records/update_labels"]
        Duplicates["duplicates.py<br/>· GET /duplicates"]
        Database["database.py<br/>· GET /database/stats<br/>· GET /database/export_embeddings<br/>· GET /database/export_duplicates<br/>· GET /database/export_compact_*<br/>· POST /database/recompute_relations<br/>· POST /database/rebuild_relations<br/>· POST /database/reannotate"]
    end

    Frontend["前端 (React)"] --> Health & Jobs & Models & Records & Duplicates & Database

    Jobs --> JobRunner["后台线程执行"]
    Database --> Maintenance["database_maintenance.py"]
```

### 路由与前端页面对照

| 页面 | 用到的 API 端点 |
|---|---|
| **Home** | `GET /health`, `GET /utils/backend_status`, `POST /utils/restart_backend` |
| **Tasks** | `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/logs`, `GET /jobs` |
| **ImageQuery** | `GET /records/by_path`, `GET /records/preview`, `POST /records/update_labels` |
| **Database** | `GET /database/stats`, `GET /database/export_*`, `POST /database/recompute_relations`, `POST /database/rebuild_relations`, `POST /database/reannotate`, `GET /records`, `GET /duplicates` |
| **Settings** | `GET /utils/read_file`, `POST /utils/write_file`, `GET /models`, `PUT /models/circuit-breaker`, `POST /models/test`, `POST /models/reset` |

---

## 5. 前端组件树

```mermaid
graph TB
    App["App.tsx<br/>BrowserRouter"]
    ThemeProvider["ThemeContext.tsx<br/>暗黑主题"]
    Layout["Layout.tsx<br/>侧边栏 + Outlet"]

    App --> ThemeProvider
    ThemeProvider --> Layout
    Layout --> Home["Home 首页（教程+服务状态）"]
    Layout --> Tasks["Tasks 标注任务"]
    Layout --> ImageQuery["ImageQuery 图片查询"]
    Layout --> Database_["Database 数据库"]
    Layout --> Settings["Settings 设置"]

    subgraph "共享"
        API_Client["api/client.ts<br/>所有 HTTP 调用"]
        AbortHook["hooks/useAbortSignal.ts<br/>请求取消"]
        TutorialContent["components/TutorialContent.tsx"]
        SystemInfo["components/SystemInfoSection.tsx"]
    end

    Home --> TutorialContent & SystemInfo
    Tasks & ImageQuery & Database_ & Settings & Home --> API_Client
```

---

## 6. 数据流向

### 6.1 标注任务执行

```mermaid
sequenceDiagram
    participant User as 用户
    participant Tasks as Tasks.tsx
    participant API as FastAPI
    participant Runner as job_runner
    participant Pipeline as pipeline.py
    participant Annotator as annotator.py
    participant Cluster as cluster_engine.py
    participant Pool as vlm_annotation_pool.py

    User->>Tasks: 填写输入目录 + 参数
    Tasks->>API: POST /jobs { input_dirs, work_dir }
    API->>Runner: submit_job(PipelineConfig)
    Runner->>Runner: 后台线程启动
    Runner->>Pipeline: run_annotation_pipeline(cfg)
    Pipeline->>Pipeline: collect_image_paths()
    Pipeline->>Annotator: start_vlm_pool()
    loop 每批图片
        Pipeline->>Annotator: process_batch(paths, images)
        Annotator->>Cluster: 双阈值建簇 + submit VLM 任务
        Note over Cluster,Pool: CLIP 与 VLM worker 并行
    end
    Pipeline->>Annotator: shutdown_vlm_pool(wait_idle)

    loop 每 2 秒
        Tasks->>API: GET /jobs/{id}
        API->>Tasks: processed/total + vlm_calls/new_centers
    end

    Pipeline-->>Runner: PipelineResult
    Runner-->>API: 状态更新
```

### 6.2 配置管理

```mermaid
sequenceDiagram
    participant Settings as Settings.tsx
    participant API as FastAPI
    participant ConfigFile as config.json
    participant Core as core/config.py

    Settings->>API: GET /api/utils/read_file?path=config.json
    API->>ConfigFile: 读取文件
    ConfigFile-->>API: JSON 内容
    API-->>Settings: { content: "{...}" }

    Note over Settings: 用户编辑各字段

    Settings->>API: POST /api/utils/write_file { path, content }
    API->>ConfigFile: 写入文件

    Note over Core: 后端重启后重新加载
    Core->>ConfigFile: import 时读取
    ConfigFile-->>Core: Settings(db_path=work_dir/subdir)
```

---

## 7. 配置项一览

```mermaid
mindmap
  ((config.json))
    基础参数
      batch_size: 32
      tau_dup: 0.05
      tau_cls: 0.25
      record_stage1_duplicates: true
    工作目录
      work_dir: "./test_work_dir"
      embedding_subdir: "embedding_index"
      duplicate_links_filename: "duplicate_links.sqlite"
    VLM 模型
      [vlm_models]
        id: "uuid（endpoint_id）"
        name: "model-name"
        base_url: "https://..."
        api_key: "sk-..."
        priority: 1
        enabled: true
      vlm_strategy: "priority / round_robin"
    熔断器
      time_window_seconds: 300
      failure_rate_threshold: 0.5
      cooldown_seconds: 600
    Questions
      scene: { description, type }
      time_of_day: { description, type, choices }
      num_of_person: { description, type, min }
      brightness: { description, type, min, max, step }
```

---

## 8. 各层职责边界

| 层级 | 目录 | 职责 |
|---|---|---|
| **核心引擎** | `core/` | 与 HTTP 无关的业务逻辑。CLIP 提取、ChromaDB 操作、VLM 调用、流水线编排。可被 CLI 和 HTTP 共用。 |
| **后端 API** | `backend/` | FastAPI 路由层。参数校验、后台任务管理、请求响应转换。调用 core 层完成业务。 |
| **前端** | `web/` | React SPA。用户交互、API 调用、状态展示。不包含业务逻辑。 |
| **CLI** | `main.py`、`view_db.py` | 命令行入口。适合调试、批量处理、无头服务器使用。 |

### 关键设计决策

1. **work_dir 统一由后端 config 管理**：前端 Settings 页写入 `config.json`，后端各路由不传 work_dir 时自动回退到 `settings.db_path`（详见 `_resolve_paths` 和 `_resolve_work_dir`）。
2. **API 无状态**：后端不维护全局 work_dir 状态；每个请求或者从参数获取，或者回退到 import-time 的 `Settings` 单例。
3. **后台任务互斥**：`job_runner.py` 用 `threading.Lock` 保证单线程执行，避免 ChromaDB 并发写入冲突。
4. **路径压缩**：`PathPrefixRegistry` 将磁盘绝对路径映射为 `(prefix_id, rel_path)`，减小 ChromaDB 存储开销。