"""
SAP 记忆图谱 API 服务 (GRAG 架构)

Graph-RAG 知识图谱后端：以五元组 (主体, 主体类型, 谓词, 客体, 客体类型) 为核心数据模型。

主要能力：
  - 五元组 CRUD 与 RAG 检索
  - 对话 Hook（对话前召回 / 对话后异步提取）
  - 3D 可视化图数据接口
"""
import os
import json
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv()

# ======================== 配置 ========================
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "memory123")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
API_PORT = int(os.getenv("MEMORY_API_PORT", "9800"))

from neo4j_backend import MemoryGraph
from memory_extractor import MemoryExtractor
from memory_hook import MemoryHook

# 全局单例，在 lifespan 中初始化
graph: Optional[MemoryGraph] = None
extractor: Optional[MemoryExtractor] = None
hook: Optional[MemoryHook] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global graph, extractor, hook
    print("[MemoryAPI] 🚀 启动中...")

    # 初始化 Neo4j 图谱、LLM 提取器、对话 Hook
    graph = MemoryGraph(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)
    extractor = MemoryExtractor(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, model=LLM_MODEL)
    hook = MemoryHook(graph, extractor)
    # 启动异步 worker，后台消费对话提取任务（非阻塞 LLM 调用）
    await graph.start_workers(num_workers=3)
    print(f"[MemoryAPI] ✅ 就绪 | Neo4j: {NEO4J_URI} | LLM: {LLM_MODEL}")
    yield

    # 清理
    if graph: graph.close()
    print("[MemoryAPI] 🔌 已关闭")


app = FastAPI(title="SAP Memory Graph (GRAG)", version="2.0.0", lifespan=lifespan)

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ======================== 请求模型 ========================

class ChatMessage(BaseModel):
    """对话消息（用于 Hook 与提取接口）"""
    user_message: str
    ai_reply: Optional[str] = None

class AddQuintupleRequest(BaseModel):
    """添加五元组的请求体"""
    subject: str
    subject_type: str = "concept"   # person|location|organization|item|concept|time|event|activity
    predicate: str
    object: str
    object_type: str = "concept"

class RAGRequest(BaseModel):
    """RAG 检索请求：关键词列表 → 格式化记忆文本"""
    keywords: List[str]
    limit: int = 10


# ======================== REST 路由 ========================
@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path(__file__).parent.parent / "index.html"
    return HTMLResponse(content=index.read_text(encoding="utf-8")) if index.exists() else HTMLResponse("<h1>SAP Memory Graph API</h1>")

@app.get("/api/health")
async def health():
    """健康检查：Neo4j 与 LLM 连接状态"""
    return {"status": "ok", "neo4j": graph.is_connected() if graph else False, "llm": extractor.client is not None if extractor else False}

@app.get("/api/memory/stats")
async def memory_stats():
    """图谱统计：五元组数、实体数、召回次数、本地备份数"""
    if not graph: raise HTTPException(503, "图谱未初始化")
    return graph.get_stats()

@app.get("/api/memory/graph-data")
async def graph_data(limit: int = 100):
    """3D 可视化数据：实体节点 + QUINTUPLE 有向边"""
    if not graph: raise HTTPException(503, "图谱未初始化")
    return graph.get_graph_data(node_limit=limit)

@app.get("/api/memory/recent")
async def recent(limit: int = 20):
    """按创建时间倒序返回最近五元组（侧栏列表用）"""
    if not graph: raise HTTPException(503, "图谱未初始化")
    return graph.recall_recent(limit=limit)

@app.get("/api/memory/important")
async def important(limit: int = 10):
    """按 access_count 倒序返回高频召回的五元组"""
    if not graph: raise HTTPException(503, "图谱未初始化")
    return graph.recall_important(limit=limit)

@app.get("/api/memory/entity/{name}/neighbors")
async def neighbors(name: str, depth: int = 2):
    """获取指定实体在图谱中的 N 跳邻居子图"""
    if not graph: raise HTTPException(503, "图谱未初始化")
    return graph.get_entity_neighbors(name, depth)

@app.post("/api/memory/add")
async def add_quintuple(req: AddQuintupleRequest):
    """添加五元组"""
    if not graph: raise HTTPException(503, "图谱未初始化")
    ids = graph.store_quintuples([{
        "subject": req.subject, "subject_type": req.subject_type,
        "predicate": req.predicate, "object": req.object, "object_type": req.object_type
    }])
    return {"stored": len(ids)}

@app.post("/api/memory/rag")
async def rag_retrieve(req: RAGRequest):
    """RAG 检索：关键词 → 格式化三元组"""
    if not graph: raise HTTPException(503, "图谱未初始化")
    text = graph.rag_retrieve(req.keywords, limit=req.limit)
    return {"text": text}

@app.post("/api/memory/extract")
async def extract(req: ChatMessage):
    """从对话中提取五元组（不存储）"""
    if not extractor: raise HTTPException(503, "提取器未初始化")
    conv = f"用户: {req.user_message}" + (f"\nAI: {req.ai_reply}" if req.ai_reply else "")
    result = extractor.extract_quintuples(conv)
    return {"quintuples": result, "count": len(result)}

@app.post("/api/chat/before")
async def chat_before(req: ChatMessage):
    """对话前 Hook：关键词 → RAG 检索 → 返回注入上下文的记忆文本"""
    if not hook: raise HTTPException(503, "Hook 未初始化")
    return {"recall_text": hook.on_before_chat(req.user_message)}

@app.post("/api/chat/after")
async def chat_after(req: ChatMessage):
    """对话后 Hook：缓冲对话，每 N 轮触发异步五元组提取"""
    if not hook: raise HTTPException(503, "Hook 未初始化")
    if req.ai_reply: hook.on_after_chat(req.user_message, req.ai_reply)
    return {"status": "ok", "buffer": len(hook.conversation_buffer)}

@app.post("/api/chat/force-extract")
async def force_extract():
    """手动触发缓冲区内容的同步提取（不等待 buffer_size 满）"""
    if not hook: raise HTTPException(503, "Hook 未初始化")
    return hook.force_extract() or {"status": "buffer_empty"}

@app.get("/api/chat/status")
async def chat_status():
    """Hook 运行状态：轮次、缓冲区、最近召回摘要、图谱统计"""
    if not hook: raise HTTPException(503, "Hook 未初始化")
    return hook.get_status()

@app.post("/api/memory/forget")
async def forget(days: int = 90, min_access: int = 0):
    """清理旧且从未被召回的五元组（access_count <= min_access）"""
    if not graph: raise HTTPException(503, "图谱未初始化")
    return {"deleted": graph.forget_old_quintuples(days, min_access)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
