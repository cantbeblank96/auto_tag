#!/bin/bash
# 启动 Vite 前端（v2）
cd "$(dirname "$0")/../web"
echo "==> 启动 Vite 前端开发服务器 (http://localhost:5173)"
echo "    API 请求会代理到 http://localhost:8000"
npm run dev