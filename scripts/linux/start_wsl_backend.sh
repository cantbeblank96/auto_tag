#!/usr/bin/env bash
# 在 WSL 内后台启动 FastAPI 后端（供 Windows 主机通过 localhost:8000 访问）。
# 需已安装 kevin_sdk 的 WSL 虚拟环境，见 scripts/linux/install_kevin_sdk_wsl.sh。
#
# 用法：bash scripts/linux/start_wsl_backend.sh
# 可选环境变量：
#   VENV_DIR   默认 $HOME/.venvs/kevin_auto_tag_wsl
#   HOST       默认 0.0.0.0
#   PORT       默认 8000

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${VENV_DIR:-$HOME/.venvs/kevin_auto_tag_wsl}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
LOG="${REPO_ROOT}/logs/wsl_backend.log"

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  echo "未找到 WSL 虚拟环境：${VENV_DIR}/bin/python" >&2
  echo "请先执行：bash scripts/linux/install_kevin_sdk_wsl.sh" >&2
  exit 1
fi

fuser -k "${PORT}/tcp" 2>/dev/null || true
mkdir -p "${REPO_ROOT}/logs"
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
cd "${REPO_ROOT}"

nohup python -m uvicorn auto_tag.backend.app:app --host "${HOST}" --port "${PORT}" > "${LOG}" 2>&1 &
disown || true

for _ in $(seq 1 25); do
  if python - <<PY 2>/dev/null; then
import urllib.request
urllib.request.urlopen("http://127.0.0.1:${PORT}/api/health", timeout=2)
print("ok")
PY
    echo "WSL_BACKEND_OK"
    exit 0
  fi
  sleep 2
done

echo "backend failed; log tail:" >&2
tail -n 40 "${LOG}" >&2 || true
exit 1
