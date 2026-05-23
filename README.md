# 🧠 SAP Memory Graph (GRAG)

**Super Agent Party 知识图谱记忆插件**

基于 **Graph-RAG 五元组架构** 的 Neo4j 知识图谱记忆 + 3D 记忆云海可视化。

## ✨ 功能

- **五元组知识图谱**：从对话中提取 `(主体, 主体类型, 谓词, 客体, 客体类型)`，构建结构化知识图谱
- **RAG 智能召回**：对话前按关键词检索相关五元组，注入 LLM 上下文
- **3D 记忆云海**：球面坐标 7 层渲染，节点按度中心性分布，边显示谓词
- **双重存储**：Neo4j 图数据库 + 本地 JSON 备份（`logs/knowledge_graph/quintuples.json`）
- **异步提取**：对话后非阻塞写入，后台 worker 消费 LLM 提取任务
- **手动管理**：添加/搜索/清理五元组，完全可控

## 🏗️ 架构

```
用户对话
  │
  ├─ 对话前 ─→ 关键词提取 ─→ Cypher RAG ─→ 格式化五元组注入上下文
  │
  └─ 对话后 ─→ 对话缓冲 ─→ 异步任务队列 ─→ LLM 五元组提取
                                              │
                                              ├─→ Neo4j (实体 + QUINTUPLE 关系)
                                              └─→ 本地 JSON 备份
                                                      │
                                              3D 可视化前端 ← REST API
```

**五元组示例：**

```
(小夜, person, 喜欢, 猫, item)
(小夜, person, 住在, 北京, location)
```

**实体类型：** `person` · `location` · `organization` · `item` · `concept` · `time` · `event` · `activity`

## 🚀 快速开始

### 1. 前置条件

- Docker（用于 Neo4j）
- Python 3.11+
- LLM API Key（DeepSeek / OpenAI 等，需支持 structured output）

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
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=memory123
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
MEMORY_API_PORT=9800
```

### 4. 访问

- **3D 记忆云海**：http://localhost:9800
- **Neo4j Web UI**：http://localhost:7474
- **API 文档**：http://localhost:9800/docs

## 📡 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查（Neo4j + LLM 状态） |
| `/api/memory/stats` | GET | 图谱统计（五元组数、实体数、召回次数、本地备份数） |
| `/api/memory/graph-data` | GET | 3D 可视化数据（实体节点 + 有向边） |
| `/api/memory/recent` | GET | 最近五元组列表 |
| `/api/memory/important` | GET | 高频召回五元组（按 access_count 排序） |
| `/api/memory/entity/{name}/neighbors` | GET | 实体 N 跳邻居子图 |
| `/api/memory/add` | POST | 添加五元组 |
| `/api/memory/rag` | POST | RAG 检索（关键词 → 格式化记忆文本） |
| `/api/memory/extract` | POST | 从对话提取五元组（不存储） |
| `/api/memory/forget` | POST | 清理旧且从未被召回的五元组 |
| `/api/chat/before` | POST | 对话前 Hook（RAG 召回） |
| `/api/chat/after` | POST | 对话后 Hook（缓冲 + 异步提取） |
| `/api/chat/force-extract` | POST | 手动触发缓冲区提取 |
| `/api/chat/status` | GET | Hook 运行状态 |

### 请求示例

**添加五元组** `POST /api/memory/add`

```json
{
  "subject": "小夜",
  "subject_type": "person",
  "predicate": "喜欢",
  "object": "猫",
  "object_type": "item"
}
```

**RAG 检索** `POST /api/memory/rag`

```json
{
  "keywords": ["小夜", "猫"],
  "limit": 10
}
```

**对话 Hook** `POST /api/chat/before` / `POST /api/chat/after`

```json
{
  "user_message": "我喜欢猫",
  "ai_reply": "好的，我记住了"
}
```

**提取五元组（不存储）** `POST /api/memory/extract`

```json
{
  "user_message": "我叫小夜，住在北京",
  "ai_reply": "你好小夜！"
}
```

响应：

```json
{
  "quintuples": [
    {
      "subject": "小夜",
      "subject_type": "person",
      "predicate": "住在",
      "object": "北京",
      "object_type": "location"
    }
  ],
  "count": 1
}
```

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

- 🟢 青色：person 人物
- 🩷 粉色：location 地点
- 🟡 黄色：item / organization 物品 / 组织
- 🟢 绿色：event / time 事件 / 时间
- 🔵 蓝色：concept 概念
- 🟣 紫色：activity 活动

**交互**：

- 打开页面时自动加载最新数据
- 点击「🔄 刷新」手动更新
- 鼠标拖拽旋转视角
- 滚轮缩放
- 点击节点查看详情（类型、度中心性）
- 侧栏搜索高亮实体
- 侧栏展示最近五元组：`主体(类型) —[谓词]→ 客体(类型)`

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
    ├── api_server.py     # FastAPI 服务（REST API）
    ├── neo4j_backend.py  # Neo4j 五元组存储 + RAG + 异步队列
    ├── memory_extractor.py  # LLM 五元组提取（Pydantic 结构化解析）
    ├── memory_hook.py    # 对话 Hook（RAG 召回 + 异步写入）
    ├── sap_bridge.py     # SAP 插件桥接
    ├── requirements.txt
    └── .env.example
```

## 📄 License

MIT
