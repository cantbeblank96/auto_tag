# auto_tag — 测试指导

本文档面向**测试人员**，说明如何在本机启动系统、执行功能测试并核对预期结果。

## 文档索引

| 文档 | 内容 |
|------|------|
| **本文件 (README.md)** | 测试环境准备、服务启动与健康检查 |
| [01_标注任务_JSON加载与提交.md](./01_标注任务_JSON加载与提交.md) | Web 标注任务页：JSON 加载表单 → 确认 → 提交 → 验收 |

更多用例可在此目录下按 `02_xxx.md` 递增补充。版本变更见 [Release_Record.md](../Release_Record.md)。

---

## 测试环境要求

| 项目 | 要求 |
|------|------|
| Python 环境 | 仓库根 **`.venv`**（由 `uv sync --extra web` 创建，见 `pyproject.toml`） |
| 仓库根目录 | 含 `auto_tag/` 的父目录，下文记为 **仓库根** |
| 网络 | VLM 打标需可访问 `config.json` 中配置的模型 API |
| GPU | 建议有 CUDA；无 GPU 时 CLIP 会较慢但仍可跑通 |
| 浏览器 | Chrome / Edge / Firefox 等现代浏览器 |

---

## 启动服务

在**仓库根**打开终端：

```bash
bash scripts/linux/setup_uv_env.sh   # 首次
source .venv/bin/activate
export PYTHONPATH=$PYTHONPATH:.

# 若端口被占用，先释放（可选）
fuser -k 8000/tcp 5020/tcp 2>/dev/null; sleep 1

# 终端 1：后端 FastAPI（端口 8000）
bash scripts/linux/run_web_backend.sh

# 终端 2：前端 Vite（端口 5020）
bash scripts/linux/run_web_frontend_v2.sh
```

### 健康检查

```bash
# 后端（响应含 version 字段，应与 auto_tag/constant.py 一致）
curl -sf http://127.0.0.1:8000/api/health

# 前端（应返回 HTTP 200）
curl -sf -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5020/
```

浏览器访问：**http://localhost:5020**（侧栏「Auto Tag」为首页，含教程与服务状态）。

> **端口说明**：前端开发服务器端口以 `auto_tag/web/vite.config.ts` 为准，当前为 **5020**；`/api` 请求由 Vite 代理到后端 **8000**。

---

## 测试数据位置

| 路径 | 说明 |
|------|------|
| `auto_tag/test/auto_tag_job.json` | 预置任务配置（输入目录、旋转、YUV 参数） |
| `auto_tag/test/test_data/` | 测试图片目录（7 张：bmp/jpg/png/webp + 3 种 yuv） |
| `auto_tag/work_dir/` | 默认运行时数据（Chroma 索引、`log/` 日志与侧车） |

---

## 通用验收原则

1. **页面提示**：操作后应出现蓝色提示条（如「已加载到表单」「已加入任务队列」）。
2. **API 一致性**：关键指标可与 `GET /api/jobs` 或 `GET /api/jobs/{job_id}` 交叉核对。
3. **失败排查**：查看 `work_dir/log/auto_tag.log`（CLI）或任务日志接口 `GET /api/jobs/{id}/logs`。
4. **配置**：`auto_tag/config.json` 含 API Key，被 gitignore；测试前确认 VLM 模型可用（可在「设置」页测试连接）。修改 config 后可在首页「重启后端」使进程重新加载。

---

## 相关文档

- 开发者手册：`notes/for_developer/`
- 版本记录：`notes/Release_Record.md`
- Agent 协作与命令速查：仓库根目录 `AGENTS.md`
