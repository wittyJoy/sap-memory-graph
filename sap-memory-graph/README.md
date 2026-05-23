# 🧠 SAP Memory Graph

**Super Agent Party 知识图谱记忆插件**

为 SAP 添加 Neo4j 知识图谱记忆 + 3D 记忆云海可视化。

## ✨ 功能

- **知识图谱记忆**：自动从对话中提取实体和关系，构建知识图谱
- **3D 记忆云海**：沉浸式 3D 可视化，节点脉动、流动粒子、实时更新
- **智能召回**：对话时自动注入相关记忆，让 AI 记住你的喜好
- **手动管理**：添加/搜索/清理记忆，完全可控
- **实时 WebSocket**：图谱变更实时推送到前端

## 🏗️ 架构

```
SAP 对话 → MemoryHook (召回/注入) → LLM 回复 → MemoryHook (提取/存储)
                                                      ↓
                                              Neo4j 知识图谱
                                                      ↓
                                              3D 可视化前端 ← WebSocket
```

## 🚀 快速开始

### 1. 前置条件

- Docker（用于 Neo4j）
- Python 3.11+
- LLM API Key（DeepSeek / OpenAI 等）

### 2. 启动

```bash
# Linux/Mac
chmod +x start.sh
./start.sh

# Windows
双击 start.bat
```

### 3. 配置

编辑 `backend/.env`：

```env
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

### 4. 访问

- **3D 记忆云海**：http://localhost:9800
- **Neo4j Web UI**：http://localhost:7474
- **API 文档**：http://localhost:9800/docs

## 📡 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/memory/stats` | GET | 图谱统计 |
| `/api/memory/graph-data` | GET | 3D 可视化数据 |
| `/api/memory/recent` | GET | 最近记忆 |
| `/api/memory/important` | GET | 重要记忆 |
| `/api/memory/add` | POST | 添加记忆 |
| `/api/memory/recall` | POST | 实体召回 |
| `/api/memory/extract` | POST | 从对话提取记忆 |
| `/api/chat/before` | POST | 对话前钩子 |
| `/api/chat/after` | POST | 对话后钩子 |
| `/api/chat/force-extract` | POST | 手动提取记忆 |
| `/api/health` | GET | 健康检查 |

## 🔌 接入 SAP

### 方式 A：通过 SAP 插件系统

1. 将整个 `sap-memory-graph` 文件夹放入 SAP 的插件目录
2. SAP 中：开发者 → 扩展 → 加载本地插件
3. 插件会自动加载到侧栏

### 方式 B：独立运行 + SAP Webhook

1. 启动记忆图谱服务
2. 在 SAP 中配置 webhook，对话前后调用：
   - 对话前：`POST http://localhost:9800/api/chat/before`
   - 对话后：`POST http://localhost:9800/api/chat/after`

## 🎨 3D 可视化

- 🟢 绿色：人物
- 🩷 粉色：地点
- 🟡 黄色：物品
- 🟢 绿色：事件
- 🔵 蓝色：概念/话题
- 🟣 紫色：记忆节点

**交互**：
- 鼠标拖拽旋转视角
- 滚轮缩放
- 点击节点查看详情
- 侧栏搜索高亮

## 📂 项目结构

```
sap-memory-graph/
├── package.json          # SAP 插件元数据
├── index.html            # 3D 记忆云海前端
├── docker-compose.yml    # Neo4j 容器
├── start.sh              # Linux/Mac 启动脚本
├── start.bat             # Windows 启动脚本
├── README.md
├── static/               # 静态资源
└── backend/
    ├── api_server.py     # FastAPI 服务
    ├── neo4j_backend.py  # Neo4j 图谱后端
    ├── memory_extractor.py  # LLM 记忆提取
    ├── memory_hook.py    # 对话钩子
    ├── requirements.txt
    └── .env.example
```

## 📄 License

MIT
