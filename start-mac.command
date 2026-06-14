#!/bin/bash
# 旅居小蜜 · 前端本地启动（Mac 双击运行）
cd "$(dirname "$0")/frontend" || exit 1

# 检查 Node
if ! command -v node >/dev/null 2>&1; then
  echo "❌ 没装 Node.js。两种装法任选其一："
  echo "   1) 官网下载安装：https://nodejs.org （选 LTS）"
  echo "   2) 有 Homebrew 的话：brew install node"
  echo ""
  echo "装完重新双击本文件。"
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

echo "✅ Node: $(node -v)"

# 首次运行装依赖
if [ ! -d node_modules ]; then
  echo "📦 首次运行，安装依赖中（几分钟）…"
  npm install || { echo "依赖安装失败"; read -n 1 -s -r; exit 1; }
fi

echo ""
echo "🚀 启动前端：http://localhost:3000"
echo "   启动后，再打开 output/xhs-cover.html，左边手机就能显示前端。"
echo "   关闭：按 Ctrl + C"
echo ""
npm run dev
