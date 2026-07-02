# Kevin Auto Tag

基于 **ChromaDB** + **CLIP** + **VLM** 的图像自动标注系统，从 `kevin_agent` 项目抽取而来。

## 目录结构

```
├── auto_tag/               # 标注系统主代码
│   ├── main.py             # CLI 入口
│   ├── view_db.py          # 数据库查看/导出工具
│   ├── config.json         # 系统参数配置（gitignored，含 API Key）
│   ├── core/               # 核心模块（配置、CLIP、Chroma、VLM、流水线、维护）
│   ├── backend/            # FastAPI 后端服务（6 组路由）
│   ├── web/                # React 前端 (Vite + Tailwind + React Query)
│   │   └── src/pages/
│   │       ├── Tasks.tsx           # 标注任务（含清除历史记录）
│   │       ├── TaskHistory.tsx     # 任务查询（后端全部历史记录）
│   │       ├── ImageQuery.tsx      # 图片查询
│   │       ├── Database.tsx        # 数据库控制台
│   │       ├── Settings.tsx        # 配置管理
│   │       ├── Tutorial.tsx        # 教程
│   │       └── About.tsx           # 关于/健康检查
│   └── scripts/            # 启动脚本（conda agent_d 自动切换）
├── AGENTS.md               # Agent 协作指南（编码规范、启动命令）
├── notes/for_developer/    # 架构与拓展文档
└── .gitignore              # config.json / .env / work_dir 均被忽略
```

## 快速开始

```bash
conda activate agent_d
export PYTHONPATH=$PYTHONPATH:.

# CLI 流水线
python -m auto_tag.main --input_dir /path/to/images --work_dir ./work

# 查看已有索引
python -m auto_tag.view_db

# Web 控制台（后端 :8000）
bash auto_tag/scripts/run_web_backend.sh
# Web 控制台（前端 Vite :5020，代理 /api → :8000）
bash auto_tag/scripts/run_web_frontend_v2.sh
```

详细文档见 `notes/for_developer/`。

## 许可

仅供内部使用。