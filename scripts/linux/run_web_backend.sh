#!/usr/bin/env bash
# 启动 FastAPI 后端
# 用法：bash scripts/linux/run_web_backend.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_uv_env.sh"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

cd "$REPO_ROOT"
exec "$PYTHON_EXEC" -m uvicorn auto_tag.backend.app:app --host "$HOST" --port "$PORT"
