"""
SAP WebSocket 桥接器
监听 SAP 的 WebSocket 消息流，在对话前后自动调用记忆钩子。
独立运行，不需要修改 SAP 源码。
"""
import os
import json
import asyncio
import websockets
from dotenv import load_dotenv

load_dotenv()

SAP_WS_URL = os.getenv("SAP_WS_URL", "ws://127.0.0.1:3456/ws")
MEMORY_API = os.getenv("MEMORY_API_URL", "http://127.0.0.1:9800")

import httpx


async def call_memory_api(path: str, data: dict) -> dict:
    """调用记忆 API"""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{MEMORY_API}{path}",
                json=data, timeout=10
            )
            return resp.json()
        except Exception as e:
            print(f"[Bridge] API 调用失败: {e}")
            return {}


async def handle_message(msg: dict):
    """处理 SAP WebSocket 消息"""
    msg_type = msg.get("type")
    data = msg.get("data", {})

    # 用户发送消息前 → 注入记忆上下文
    if msg_type == "trigger_send_message":
        # 获取最新用户消息
        user_text = data.get("text", "")
        if user_text:
            result = await call_memory_api("/api/chat/before", {
                "user_message": user_text
            })
            recall = result.get("recall_text", "")
            if recall:
                print(f"[Bridge] 📌 注入记忆: {recall[:80]}...")
                # 注意：SAP 的 systemPrompt 注入需要通过其 API
                # 这里记录日志，实际注入方式取决于 SAP 版本

    # AI 回复后 → 提取记忆
    elif msg_type in ("messages_update", "broadcast_messages"):
        messages = data.get("messages", [])
        if len(messages) >= 2:
            last = messages[-1]
            prev = messages[-2]
            if last.get("role") == "assistant" and prev.get("role") == "user":
                await call_memory_api("/api/chat/after", {
                    "user_message": prev.get("pure_content", ""),
                    "ai_reply": last.get("pure_content", "")
                })


async def bridge_loop():
    """主循环：连接 SAP WebSocket"""
    print(f"[Bridge] 🔗 连接 SAP: {SAP_WS_URL}")
    while True:
        try:
            async with websockets.connect(SAP_WS_URL) as ws:
                print("[Bridge] ✅ 已连接 SAP")
                # 请求当前消息
                await ws.send(json.dumps({"type": "get_messages"}))

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        await handle_message(msg)
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"[Bridge] ❌ 连接断开: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════╗
║   🔗 SAP Memory Bridge                  ║
║   监听 SAP 消息，自动记忆提取/注入       ║
╚══════════════════════════════════════════╝
    """)
    asyncio.run(bridge_loop())
