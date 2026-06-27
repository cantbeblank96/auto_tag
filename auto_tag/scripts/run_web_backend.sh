#!/usr/bin/env bash
# 启动 FastAPI：供 Streamlit 通过 HTTP 调用
# 用法：bash auto_tag/scripts/run_web_backend.sh
# 可覆盖：HOST、PORT、PYTHON_EXECUTABLE

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_conda_agent_d.sh"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

cd "$REPO_ROOT"
exec "$PYTHON_EXEC" -m uvicorn auto_tag.backend.app:app --host "$HOST" --port "$PORT"
