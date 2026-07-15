# Auto Tag

**[English](./README.en.md)** | **简体中文**

基于 **ChromaDB** + **CLIP** + **VLM** 的图像自动标注系统，从 `kevin_agent` 项目抽取而来。

## 目录结构

```
├── auto_tag/               # 标注系统主代码
├── pyproject.toml          # uv 项目定义
├── uv.lock                 # uv 锁定依赖
├── scripts/
│   ├── linux/              # Linux/macOS 脚本（bash）
│   └── windows/            # Windows 脚本（PowerShell）
├── notes/                  # 文档（Release_Record、for_developer、for_test）
├── temp/                   # 本地导出/临时文件（gitignore，勿提交）
└── LICENSE                 # MIT
```

当前版本见 [notes/Release_Record.md](./notes/Release_Record.md)（**v0.0.4**）。Web 控制台：http://localhost:5020

## 日常使用（Windows，非技术向）

安装和首次配置仍需维护同学协助完成。你只需要会**启动、使用、关闭**即可。

### 打开控制台（启动）

1. 用资源管理器打开仓库里的文件夹：`scripts\windows`
2. **双击** `start_web.bat`
3. 等黑窗口提示「启动成功」（大约几十秒；首次可能更久）
4. 浏览器会尽量自动打开；若没有，请自己访问：**http://localhost:5020**
5. 看到黑窗口的成功提示后，窗口可以关掉——服务会在后台继续跑

### 关闭控制台（不用时）

1. 同样进入 `scripts\windows`
2. **双击** `stop_web.bat`
3. 看到「关闭完成」即可关掉窗口

不关也可以，但长期挂着会占显卡/内存，下班或不跑任务时建议关掉。

### 重启（页面打不开、报错卡住时）

1. **双击** `restart_web.bat`（等于先关闭再启动）
2. 成功后再打开 **http://localhost:5020**

### 使用时请记住

| 做什么 | 怎么做 |
|--------|--------|
| 打开界面 | 浏览器访问 http://localhost:5020 |
| 设置模型 / 工作目录 | 左侧菜单「设置」 |
| 提交标注任务 | 左侧菜单「任务」 |
| 看库里有多少图 | 左侧菜单「数据库」 |

**任务一次只能跑一个。** 已有任务在运行时不要连点多次「提交」，等当前任务完成后再交下一个。

### 常见问题

- **双击 bat 一闪而过 / 提示找不到 Python**  
  多半环境没装好。把黑窗口完整截图发给维护同学，不要反复乱点。
- **浏览器打不开或一直转圈**  
  先 `restart_web.bat`，仍不行再把 `%TEMP%\auto_tag_web_backend.log` 和 `auto_tag_web_frontend.log`（在用户临时目录）交给维护同学。
- **提示端口被占用**  
  先 `stop_web.bat`，等几秒再 `start_web.bat`。

---

## 快速开始（开发 / 首次安装）

首次使用请复制配置模板（**勿将含真实 API Key 的 `config.json` 提交到 Git**）：

```bash
cp auto_tag/config.example.json auto_tag/config.json
cp auto_tag/.env.example auto_tag/.env   # 可选，兼容旧版单模型环境变量
```

### Linux / macOS

```bash
# 检查或安装 uv
bash scripts/linux/test_uv.sh
# 或：bash scripts/linux/ensure_uv.sh

# 创建 .venv 并安装依赖
bash scripts/linux/setup_uv_env.sh

source .venv/bin/activate
export PYTHONPATH=$PYTHONPATH:.

python -m auto_tag.main --input_dir /path/to/images --work_dir ./work

# Web 控制台日常启停（后台）
bash scripts/linux/start_web.sh     # 启动后端+前端
bash scripts/linux/stop_web.sh      # 关闭
bash scripts/linux/restart_web.sh   # 重启
# 浏览器打开：http://localhost:5020
# 日志：logs/auto_tag_web_backend.log / logs/auto_tag_web_frontend.log
```

前台手动启动（关终端即停）：

```bash
bash scripts/linux/run_web_backend.sh
bash scripts/linux/run_web_frontend_v2.sh
```

### Windows（PowerShell）

```powershell
# 检查或安装 uv
powershell -ExecutionPolicy Bypass -File scripts/windows/test_uv.ps1

# 创建 .venv 并安装依赖
powershell -ExecutionPolicy Bypass -File scripts/windows/setup_uv_env.ps1

.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "$PWD"

python -m auto_tag.main --input_dir D:\images --work_dir .\work
```

日常启停推荐直接双击（无需记命令）：

| 脚本 | 作用 |
|------|------|
| `scripts\windows\start_web.bat` | 一键启动前后端 |
| `scripts\windows\stop_web.bat` | 一键关闭 |
| `scripts\windows\restart_web.bat` | 一键重启 |

> 说明：`.bat` 窗口提示为英文（避免 Windows 默认编码把中文 bat 解析乱）；实际关闭/启动逻辑在同目录的 `.ps1` 中，中文说明仍会正常显示。

也可以在 PowerShell 中手动启动（前台窗口，关窗即停）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_backend.ps1
powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_frontend_v2.ps1
```

## License

本项目采用 [MIT License](./LICENSE)。
