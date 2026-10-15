#!/bin/bash
cd "$(dirname "$0")"
echo "🚀 正在启动研报辅助 Agent..."
echo ""
echo "📊 启动后会自动打开浏览器 http://localhost:8000"
echo "⏹️ 按 Ctrl+C 停止服务"
echo ""

# 等2秒后自动打开浏览器
(sleep 3 && open http://localhost:8000) &

python main.py serve
