#!/usr/bin/env bash
# 启动 Streamlit 控制台（需先另开终端运行 run_web_backend.sh）
# 用法：bash auto_tag/scripts/run_web_frontend.sh
# 可覆盖：AUTO_TAG_API_BASE、PYTHON_EXECUTABLE

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_conda_agent_d.sh"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"
export AUTO_TAG_API_BASE="${AUTO_TAG_API_BASE:-http://127.0.0.1:8000}"

cd "$REPO_ROOT"
exec "$PYTHON_EXEC" -m streamlit run auto_tag/frontend_streamlit/app.py
