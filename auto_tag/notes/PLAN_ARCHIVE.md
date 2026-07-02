# 架构与迭代计划归档（执行摘要）

本文件为对话中确认的产品/技术决策摘要，与实现细节以 [IMPLEMENTATION_AND_ARCHITECTURE.md](./IMPLEMENTATION_AND_ARCHITECTURE.md) 为准。

## 已落地

1. **Stage 1 冗余路径**：方案 B，侧车 `log_dir/duplicate_links.jsonl`；`query_batch` 返回近邻 `ids` 作为 `anchor_id`。
2. **Web**：**双进程 HTTP** — `auto_tag/backend/`（FastAPI）+ `auto_tag/frontend_streamlit/`（Streamlit + httpx），均在 `auto_tag` 目录下管理。
3. **核心抽取**：`auto_tag/core/pipeline.py` 供 CLI 与后端共用；`main.py` 仅参数与交互确认。
4. **混合 YUV**：`load_image_for_job` + CLI/API `mixed_yuv`；`--b_mixed_yuv` + 宽高用于 JPG/NV21 同目录。
5. **递归列目录**：`core.pipeline._walk_collect_images`，适配深层文件夹结构。
6. **核心包**：`auto_tag/core/` 集中存放 `annotator`、`pipeline`、`config` 等，根目录仅 CLI / 文档。

## 演进（未做）

- VLM 并发/队列、SQLite 版 duplicate 索引、缩略图 API 路径白名单、SPA 前端替换 Streamlit。

## 试采测试参数备忘

- 路径：`/SDA/data_aqu_2024-12-26/试采/Pictures/Front`
- NV21：`640×480`；旋转：`ROTATE_90_COUNTERCLOCKWISE`（逆时针 90°）
- 命令见 IMPLEMENTATION 文档 §7。
