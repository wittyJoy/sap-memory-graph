"""
对话管道 Hook — 记忆召回与注入
Intercepts conversations to recall relevant memories before LLM,
and extract new memories after LLM responds.
"""
import time
from typing import Optional
from collections import deque

from neo4j_backend import MemoryGraph
from memory_extractor import MemoryExtractor


class MemoryHook:
    """对话记忆钩子，负责在对话前后注入/提取记忆"""

    def __init__(self, graph: MemoryGraph, extractor: MemoryExtractor,
                 buffer_size: int = 5, recall_limit: int = 5,
                 auto_extract: bool = True):
        """
        Args:
            graph: Neo4j 图谱实例
            extractor: 记忆提取器实例
            buffer_size: 每 N 轮对话提取一次记忆
            recall_limit: 每次召回的最大记忆数
            auto_extract: 是否自动提取记忆
        """
        self.graph = graph
        self.extractor = extractor
        self.buffer_size = buffer_size
        self.recall_limit = recall_limit
        self.auto_extract = auto_extract

        # 对话缓冲区
        self.conversation_buffer: deque = deque(maxlen=buffer_size * 2)
        # 轮次计数
        self.turn_count = 0
        # 上下文缓存（当前召回的记忆文本）
        self._last_recall_text = ""

    def on_before_chat(self, user_message: str) -> str:
        """
        对话前钩子：从知识图谱中召回与当前消息相关的记忆

        Args:
            user_message: 用户输入的消息

        Returns:
            注入到 system prompt 中的记忆文本，无记忆则返回空字符串
        """
        if not self.graph.is_connected():
            return ""

        # 1. 快速提取当前消息中的实体
        entities = self.extractor.quick_extract_entities(user_message)

        # 2. 从图谱中召回相关记忆
        memories = []
        if entities:
            memories = self.graph.recall_by_entities(
                entities, limit=self.recall_limit
            )

        # 3. 如果实体召回不够，补充最近的重要记忆
        if len(memories) < 2:
            recent = self.graph.recall_recent(limit=3)
            existing_ids = {m["id"] for m in memories}
            for m in recent:
                if m["id"] not in existing_ids:
                    memories.append(m)
                if len(memories) >= self.recall_limit:
                    break

        if not memories:
            self._last_recall_text = ""
            return ""

        # 4. 格式化记忆文本
        memory_lines = []
        for m in memories:
            mtype = m.get("type", "episodic")
            icon = {"episodic": "📌", "semantic": "📚",
                    "emotional": "💖"}.get(mtype, "📝")
            memory_lines.append(f"{icon} {m['content']}")

        recall_text = (
            "\n\n【你记得的事情】\n"
            + "\n".join(memory_lines)
            + "\n"
        )
        self._last_recall_text = recall_text
        return recall_text

    def on_after_chat(self, user_message: str, ai_reply: str):
        """
        对话后钩子：缓冲对话，定期提取记忆存入图谱

        Args:
            user_message: 用户消息
            ai_reply: AI 回复
        """
        # 1. 记录到缓冲区
        self.conversation_buffer.append({
            "user": user_message,
            "ai": ai_reply,
            "timestamp": time.time()
        })
        self.turn_count += 1

        # 2. 判断是否需要提取记忆
        if (self.auto_extract and
                self.turn_count % self.buffer_size == 0):
            self._extract_and_store()

    def _extract_and_store(self):
        """从缓冲区中提取记忆并存入图谱"""
        if not self.conversation_buffer:
            return

        # 拼接缓冲区对话
        conversation = "\n".join([
            f"用户: {item['user']}\nAI: {item['ai']}"
            for item in self.conversation_buffer
        ])

        # 用 LLM 提取记忆要素
        memory_data = self.extractor.extract_memory(conversation)
        if not memory_data or not memory_data.get("summary"):
            return

        # 过滤低重要性记忆
        if memory_data.get("importance", 0) < 0.3:
            return

        # 存入图谱
        mid = self.graph.add_memory(
            content=memory_data["summary"],
            entities=memory_data.get("entities", []),
            relations=memory_data.get("relations", []),
            memory_type=memory_data.get("memory_type", "episodic"),
            importance=memory_data.get("importance", 0.5),
            source="conversation",
            tags=memory_data.get("tags", [])
        )

        if mid:
            print(f"[MemoryHook] ✅ 新记忆 #{mid}: "
                  f"{memory_data['summary'][:50]}... "
                  f"(重要性: {memory_data.get('importance', 0.5):.1f})")

        # 清空缓冲区
        self.conversation_buffer.clear()

    def force_extract(self) -> Optional[dict]:
        """手动触发一次记忆提取（不等待缓冲区满）"""
        if not self.conversation_buffer:
            return None
        self._extract_and_store()
        return {"status": "extracted"}

    def get_status(self) -> dict:
        """获取记忆钩子状态"""
        return {
            "connected": self.graph.is_connected(),
            "turn_count": self.turn_count,
            "buffer_size": len(self.conversation_buffer),
            "buffer_capacity": self.buffer_size,
            "auto_extract": self.auto_extract,
            "last_recall": self._last_recall_text[:100] if self._last_recall_text else None
        }
