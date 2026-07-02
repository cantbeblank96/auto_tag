# Auto Tag

**English** | **[简体中文](./README.md)**

Image auto-annotation pipeline built on **ChromaDB** + **CLIP** + **VLM**, extracted from the `kevin_agent` project.

## Repository layout

```
├── auto_tag/               # Core application
├── pyproject.toml          # uv project definition
├── uv.lock                 # Locked dependencies
├── scripts/
│   ├── linux/              # Linux/macOS (bash)
│   └── windows/            # Windows (PowerShell)
├── notes/                  # Docs (Release_Record, for_developer, for_test)
└── LICENSE                 # MIT
```

Current version: [notes/Release_Record.md](./notes/Release_Record.md) (**v0.0.2**). Web console: http://localhost:5020

## Quick start

Copy the config templates first (**never commit a real `config.json` with API keys**):

```bash
cp auto_tag/config.example.json auto_tag/config.json
cp auto_tag/.env.example auto_tag/.env   # optional, legacy single-model env vars
```

### Linux / macOS

```bash
# Check or install uv
bash scripts/linux/test_uv.sh
# or: bash scripts/linux/ensure_uv.sh

# Create .venv and install dependencies
bash scripts/linux/setup_uv_env.sh

source .venv/bin/activate
export PYTHONPATH=$PYTHONPATH:.

python -m auto_tag.main --input_dir /path/to/images --work_dir ./work
bash scripts/linux/run_web_backend.sh
bash scripts/linux/run_web_frontend_v2.sh
```

### Windows (PowerShell)

```powershell
# Check or install uv
powershell -ExecutionPolicy Bypass -File scripts/windows/test_uv.ps1

# Create .venv and install dependencies
powershell -ExecutionPolicy Bypass -File scripts/windows/setup_uv_env.ps1

.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "$PWD"

python -m auto_tag.main --input_dir D:\images --work_dir .\work
powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_backend.ps1
powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_frontend_v2.ps1
```

## License

This project is licensed under the [MIT License](./LICENSE).
