#!/usr/bin/env bash
# 在 WSL 内安装 kevin_sdk 及 Auto Tag WSL 专用虚拟环境（与 Windows .venv 分离）。
#
# 前置：
#   - WSL2 + Ubuntu（或兼容发行版）
#   - 仓库位于 Windows 盘符并通过 /mnt/<盘符>/... 挂载（如 /mnt/d/dev/kevin_auto_tag）
#   - kevin_sdk wheel 与 models 已放到同级目录（默认 ../kevin_sdk，可通过 KEVIN_SDK_ROOT 覆盖）
#   - 证书已复制到 /mnt/c/Users/<you>/.kv_sdk_cfg/licenses.lic（或 LICENSE_SRC 指定）
#
# 用法：bash scripts/linux/install_kevin_sdk_wsl.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${VENV_DIR:-$HOME/.venvs/kevin_auto_tag_wsl}"
KEVIN_SDK_ROOT="${KEVIN_SDK_ROOT:-$(cd "$REPO_ROOT/../kevin_sdk" 2>/dev/null && pwd || true)}"
WHEEL="${KEVIN_SDK_ROOT}/release/v0-0-3/kevin_sdk-0.0.3-py3-none-any.whl"
LICENSE_SRC="${LICENSE_SRC:-/mnt/c/Users/${USER}/.kv_sdk_cfg/licenses.lic}"

export PATH="${HOME}/.local/bin:${PATH:-}"

if [ ! -f "$WHEEL" ]; then
  echo "未找到 wheel：$WHEEL" >&2
  echo "请设置 KEVIN_SDK_ROOT 或将 kevin_sdk 放在仓库同级目录。" >&2
  exit 1
fi

echo "==> ensure uv"
cd "$REPO_ROOT"
bash scripts/linux/ensure_uv.sh

echo "==> uv sync -> ${VENV_DIR}"
export VENV_DIR
bash scripts/linux/setup_uv_env.sh

echo "==> install kevin_sdk"
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
uv pip install "${WHEEL}" ctypesgen

echo "==> license"
mkdir -p "${HOME}/.kv_sdk_cfg"
if [ ! -f "$LICENSE_SRC" ]; then
  echo "未找到证书：$LICENSE_SRC" >&2
  echo "请先将 .lic 复制到 Windows 用户目录 .kv_sdk_cfg/licenses.lic，或设置 LICENSE_SRC。" >&2
  exit 1
fi
cp "$LICENSE_SRC" "${HOME}/.kv_sdk_cfg/licenses.lic"
printf '%s\n' "{\"license_path\":\"${HOME}/.kv_sdk_cfg/licenses.lic\"}" > "${HOME}/.kv_sdk_cfg/.path.json"

echo "==> verify detect (optional sample under /mnt/d/dev/little_data/hunhe)"
python - <<'PY'
import glob
import os

import cv2
from kevin_sdk.api import build_model, detect

model_path = os.environ.get(
    "DETECT_MODEL",
    "/mnt/d/dev/kevin_sdk/models/detection/M_Detect_Hunter_SmallFace_IR_AND_RGB_360_9.2.5.model",
)
if not os.path.isfile(model_path):
    print("skip detect smoke: model not found", model_path)
    print("WSL_VERIFY_OK")
    raise SystemExit(0)

handle = build_model(model_type="detect", model_path=model_path)
imgs = glob.glob("/mnt/d/dev/little_data/hunhe/*.jpg")[:1]
if not imgs:
    print("skip detect smoke: no sample images")
    print("WSL_VERIFY_OK")
    raise SystemExit(0)

res = detect(image=cv2.imread(imgs[0]), model=handle, b_parse_result=True)
print("faces", len(res) if res else 0, "sample", imgs[0])
print("WSL_VERIFY_OK")
PY

echo "==> done"
