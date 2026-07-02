#!/usr/bin/env bash
# 检查系统是否已安装 uv；未安装时可选自动安装（官方 install.sh）。
# 用法：bash scripts/linux/ensure_uv.sh
# 环境变量：AUTO_INSTALL_UV=0 仅检查不安装（缺失时 exit 1）

set -euo pipefail

_add_local_bin_to_path() {
  export PATH="${HOME}/.local/bin:${PATH:-}"
}

_install_uv() {
  echo "==> 未检测到 uv，正在通过官方脚本安装..."
  if ! command -v curl >/dev/null 2>&1; then
    echo "错误：需要 curl 才能自动安装 uv。" >&2
    echo "请手动安装：https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  _add_local_bin_to_path
}

_add_local_bin_to_path

if command -v uv >/dev/null 2>&1; then
  echo "uv 已安装：$(uv --version)"
  exit 0
fi

if [ "${AUTO_INSTALL_UV:-1}" = "0" ]; then
  echo "未找到 uv。可执行：bash scripts/linux/ensure_uv.sh" >&2
  exit 1
fi

_install_uv

if ! command -v uv >/dev/null 2>&1; then
  echo "安装后仍无法在 PATH 中找到 uv。请将 ~/.local/bin 加入 PATH 后重试。" >&2
  exit 1
fi

echo "uv 安装完成：$(uv --version)"
