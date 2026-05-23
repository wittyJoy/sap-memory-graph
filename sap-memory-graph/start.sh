#!/bin/bash
# SAP Memory Graph - 启动脚本
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "╔══════════════════════════════════════════╗"
echo "║   🧠 SAP Memory Graph                    ║"
echo "╚══════════════════════════════════════════╝"

# 1. 检查 .env
if [ ! -f backend/.env ]; then
    echo "📝 创建 .env 配置文件..."
    cp backend/.env.example backend/.env
    echo "⚠️  请编辑 backend/.env 填入你的 LLM API Key"
fi

# 2. 启动 Neo4j
echo "🗄️  启动 Neo4j..."
docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null

# 等待 Neo4j 就绪
echo "⏳ 等待 Neo4j 就绪..."
for i in {1..30}; do
    if curl -s http://localhost:7474 > /dev/null 2>&1; then
        echo "✅ Neo4j 就绪"
        break
    fi
    sleep 1
done

# 3. 安装 Python 依赖
if [ ! -d "backend/.venv" ]; then
    echo "🐍 创建 Python 虚拟环境..."
    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt -q
    cd ..
else
    source backend/.venv/bin/activate
fi

# 4. 启动 API 服务
echo "🚀 启动 API 服务..."
cd backend
python api_server.py
