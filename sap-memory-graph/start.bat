@echo off
chcp 65001 >nul 2>&1
title SAP Memory Graph

echo ╔══════════════════════════════════════════╗
echo ║   🧠 SAP Memory Graph                    ║
echo ╚══════════════════════════════════════════╝

REM 1. 检查 .env
if not exist backend\.env (
    echo 📝 创建 .env 配置文件...
    copy backend\.env.example backend\.env
    echo ⚠️  请编辑 backend\.env 填入你的 LLM API Key
)

REM 2. 启动 Neo4j
echo 🗄️  启动 Neo4j...
docker compose up -d 2>nul || docker-compose up -d 2>nul

echo ⏳ 等待 Neo4j 就绪...
timeout /t 10 /nobreak >nul

REM 3. 安装 Python 依赖
if not exist backend\.venv (
    echo 🐍 创建 Python 虚拟环境...
    cd backend
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt -q
    cd ..
) else (
    call backend\.venv\Scripts\activate.bat
)

REM 4. 启动 API 服务
echo 🚀 启动 API 服务...
cd backend
python api_server.py
pause
