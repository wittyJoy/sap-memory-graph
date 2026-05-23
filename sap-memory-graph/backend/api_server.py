"""
SAP 记忆图谱 API 服务
FastAPI backend for the SAP Memory Graph plugin.
Provides REST API + WebSocket for the frontend 3D visualization.
"""
import os
import json
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ======================== 配置 ========================
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "memory123")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

SAP_WS_URL = os.getenv("SAP_WS_URL", "ws://127.0.0.1:3456/ws")
API_PORT = int(os.getenv("MEMORY_API_PORT", "9800"))

# ======================== 导入后端模块 ========================
from neo4j_backend import MemoryGraph
from memory_extractor import MemoryExtractor
from memory_hook import MemoryHook

# ======================== 全局实例 ========================
graph: Optional[MemoryGraph] = None
extractor: Optional[MemoryExtractor] = None
hook: Optional[MemoryHook] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global graph, extractor, hook
    print("[MemoryAPI] 🚀 启动中...")

    # 初始化组件
    graph = MemoryGraph(
        uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD
    )
    extractor = MemoryExtractor(
        api_key=LLM_API_KEY, base_url=LLM_BASE_URL, model=LLM_MODEL
    )
    hook = MemoryHook(graph, extractor)

    print(f"[MemoryAPI] ✅ 就绪 | Neo4j: {NEO4J_URI} | LLM: {LLM_MODEL}")
    yield

    # 清理
    if graph:
        graph.close()
    print("[MemoryAPI] 🔌 已关闭")


app = FastAPI(
    title="SAP Memory Graph",
    description="知识图谱记忆 + 3D 记忆云海",
    version="1.0.0",
    lifespan=lifespan
)

# 静态文件
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ======================== Pydantic 模型 ========================

class ChatMessage(BaseModel):
    user_message: str
    ai_reply: Optional[str] = None

class AddMemoryRequest(BaseModel):
    content: str
    entities: list = []
    relations: list = []
    memory_type: str = "episodic"
    importance: float = 0.5
    tags: list = []

class RecallRequest(BaseModel):
    entities: list = []
    limit: int = 10

class EntityRequest(BaseModel):
    name: str
    entity_type: str = "Concept"
    properties: dict = {}

class RelationRequest(BaseModel):
    source: str
    target: str
    relation: str
    properties: dict = {}


# ======================== API 路由 ========================

@app.get("/", response_class=HTMLResponse)
async def root():
    """服务主页"""
    index_path = Path(__file__).parent.parent / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>SAP Memory Graph API</h1><p>API 运行中</p>")


@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "neo4j": graph.is_connected() if graph else False,
        "llm": extractor.client is not None if extractor else False
    }


@app.get("/api/memory/stats")
async def memory_stats():
    """获取图谱统计信息"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    return graph.get_stats()


@app.get("/api/memory/graph-data")
async def graph_data(limit: int = 300):
    """获取 3D 可视化用的图数据"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    return graph.get_graph_data(node_limit=limit)


@app.get("/api/memory/entity/{name}/neighbors")
async def entity_neighbors(name: str, depth: int = 2):
    """获取实体的邻居子图"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    return graph.get_entity_neighbors(name, depth)


@app.get("/api/memory/recent")
async def recent_memories(limit: int = 20, type: str = None):
    """获取最近的记忆"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    return graph.recall_recent(limit=limit, memory_type=type)


@app.get("/api/memory/important")
async def important_memories(limit: int = 10):
    """获取最重要的记忆"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    return graph.recall_important(limit=limit)


@app.post("/api/memory/add")
async def add_memory(req: AddMemoryRequest):
    """手动添加一条记忆"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    mid = graph.add_memory(
        content=req.content,
        entities=req.entities,
        relations=req.relations,
        memory_type=req.memory_type,
        importance=req.importance,
        tags=req.tags
    )
    if mid is None:
        raise HTTPException(500, "添加记忆失败")
    return {"id": mid, "status": "ok"}


@app.post("/api/memory/recall")
async def recall_memories(req: RecallRequest):
    """根据实体召回记忆"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    return graph.recall_by_entities(req.entities, limit=req.limit)


@app.post("/api/memory/extract")
async def extract_from_chat(req: ChatMessage):
    """从对话中提取记忆（不存储，仅返回提取结果）"""
    if not extractor:
        raise HTTPException(503, "提取器未初始化")
    conversation = f"用户: {req.user_message}"
    if req.ai_reply:
        conversation += f"\nAI: {req.ai_reply}"
    result = extractor.extract_memory(conversation)
    return result


@app.post("/api/chat/before")
async def chat_before(req: ChatMessage):
    """对话前钩子：召回记忆"""
    if not hook:
        raise HTTPException(503, "Hook 未初始化")
    recall_text = hook.on_before_chat(req.user_message)
    return {"recall_text": recall_text}


@app.post("/api/chat/after")
async def chat_after(req: ChatMessage):
    """对话后钩子：提取记忆"""
    if not hook:
        raise HTTPException(503, "Hook 未初始化")
    if req.ai_reply:
        hook.on_after_chat(req.user_message, req.ai_reply)
    return {"status": "ok", "buffer_size": len(hook.conversation_buffer)}


@app.post("/api/chat/force-extract")
async def force_extract():
    """手动触发记忆提取"""
    if not hook:
        raise HTTPException(503, "Hook 未初始化")
    result = hook.force_extract()
    return result or {"status": "buffer_empty"}


@app.get("/api/chat/status")
async def chat_status():
    """获取 Hook 状态"""
    if not hook:
        raise HTTPException(503, "Hook 未初始化")
    return hook.get_status()


@app.post("/api/entity/add")
async def add_entity(req: EntityRequest):
    """添加实体"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    graph.upsert_entity(req.name, req.entity_type, req.properties)
    return {"status": "ok"}


@app.post("/api/relation/add")
async def add_relation(req: RelationRequest):
    """添加关系"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    graph.upsert_relation(req.source, req.target, req.relation, req.properties)
    return {"status": "ok"}


@app.post("/api/memory/forget")
async def forget_old(days: int = 90, min_importance: float = 0.3):
    """清理旧的低重要性记忆"""
    if not graph:
        raise HTTPException(503, "图谱未初始化")
    deleted = graph.forget_old_memories(days, min_importance)
    return {"deleted": deleted}


# ======================== WebSocket ========================

class ConnectionManager:
    """WebSocket 连接管理"""
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        for ws in self.active[:]:
            try:
                await ws.send_json(data)
            except Exception:
                self.active.remove(ws)

ws_manager = ConnectionManager()


@app.websocket("/ws/memory")
async def websocket_memory(ws: WebSocket):
    """WebSocket：实时图谱更新推送"""
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            cmd = data.get("type")

            if cmd == "get_graph_data":
                gd = graph.get_graph_data() if graph else {"nodes": [], "edges": []}
                await ws.send_json({"type": "graph_data", "data": gd})

            elif cmd == "get_stats":
                stats = graph.get_stats() if graph else {}
                await ws.send_json({"type": "stats", "data": stats})

            elif cmd == "add_memory":
                mid = graph.add_memory(
                    content=data.get("content", ""),
                    entities=data.get("entities", []),
                    relations=data.get("relations", []),
                    importance=data.get("importance", 0.5)
                ) if graph else None
                await ws.send_json({"type": "memory_added", "id": mid})
                # 广播更新
                if graph:
                    await ws_manager.broadcast({
                        "type": "graph_update",
                        "data": graph.get_graph_data()
                    })

    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ======================== 启动 ========================

if __name__ == "__main__":
    import uvicorn
    print(f"""
╔══════════════════════════════════════════╗
║   🧠 SAP Memory Graph API Server        ║
║   http://127.0.0.1:{API_PORT}                ║
║   Neo4j: {NEO4J_URI:<30s} ║
╚══════════════════════════════════════════╝
    """)
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
