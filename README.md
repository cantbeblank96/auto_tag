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
└── LICENSE                 # MIT
```

当前版本见 [notes/Release_Record.md](./notes/Release_Record.md)（**v0.0.2**）。Web 控制台：http://localhost:5020

## 快速开始

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
powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_backend.ps1
powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_frontend_v2.ps1
```

## License

本项目采用 [MIT License](./LICENSE)。
