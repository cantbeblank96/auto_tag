#!/usr/bin/env bash
# 由 API /utils/restart_backend 调用：释放端口后拉起后端。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT="${PORT:-8000}"
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="${AUTO_TAG_BACKEND_LOG:-${LOG_DIR}/auto_tag_backend.log}"

sleep 1.0
fuser -k "${PORT}/tcp" 2>/dev/null || true
sleep 0.6

cd "$REPO_ROOT"
exec bash "$SCRIPT_DIR/run_web_backend.sh" >>"$LOG_FILE" 2>&1
