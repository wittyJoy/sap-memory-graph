"""
对话管道 Hook — GRAG 记忆召回与注入
基于 NagaAgent 的 RAG 检索模式改造

流程：
  对话前：关键词提取 → Cypher 查询 → 格式化三元组注入上下文
  对话后：异步任务队列 → 五元组提取 → 存入 Neo4j + 本地 JSON
"""
import time
import asyncio
from typing import Optional
from collections import deque

from neo4j_backend import MemoryGraph
from memory_extractor import MemoryExtractor


class MemoryHook:
    """对话记忆钩子（GRAG 架构）"""

    def __init__(self, graph: MemoryGraph, extractor: MemoryExtractor,
                 buffer_size: int = 3, recall_limit: int = 8,
                 auto_extract: bool = True):
        """
        Args:
            graph: Neo4j 图谱实例
            extractor: 五元组提取器实例
            buffer_size: 每 N 轮对话触发一次异步提取
            recall_limit: RAG 检索最大返回条数
            auto_extract: 是否自动提取
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
        # 最近一次召回的文本
        self._last_recall_text = ""

    def on_before_chat(self, user_message: str) -> str:
        """
        对话前钩子：RAG 检索相关记忆

        流程：
        1. 快速提取用户消息中的关键词
        2. 用关键词查询 Neo4j 知识图谱
        3. 格式化为 "主体(类型) —[谓词]→ 客体(类型)" 注入上下文
        """
        if not self.graph.is_connected():
            return ""

        # 1. 提取关键词
        keywords = self.extractor.quick_extract_keywords(user_message)

        if not keywords:
            self._last_recall_text = ""
            return ""

        # 2. RAG 检索
        rag_text = self.graph.rag_retrieve(keywords, limit=self.recall_limit)

        if not rag_text:
            self._last_recall_text = ""
            return ""

        # 3. 格式化注入文本
        recall_text = (
            "\n\n【记忆图谱 - 你知道的事情】\n"
            + rag_text
            + "\n"
        )
        self._last_recall_text = recall_text
        return recall_text

    def on_after_chat(self, user_message: str, ai_reply: str):
        """
        对话后钩子：缓冲对话，定期异步提取五元组
        """
        self.conversation_buffer.append({
            "user": user_message,
            "ai": ai_reply,
            "timestamp": time.time()
        })
        self.turn_count += 1

        # 每 N 轮触发一次异步提取
        if (self.auto_extract and
                self.turn_count % self.buffer_size == 0):
            self._trigger_async_extract()

    def _trigger_async_extract(self):
        """触发异步记忆提取（非阻塞）"""
        if not self.conversation_buffer:
            return

        conversation = "\n".join([
            f"用户: {item['user']}\nAI: {item['ai']}"
            for item in self.conversation_buffer
        ])

        # 尝试加入异步队列（FastAPI 事件循环中 ensure_future 非阻塞提交）
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(
                    self.graph.enqueue_task(conversation, self.extractor)
                )
            else:
                loop.run_until_complete(
                    self.graph.enqueue_task(conversation, self.extractor)
                )
        except RuntimeError:
            # 没有事件循环，在当前线程同步执行
            self._sync_extract(conversation)

        self.conversation_buffer.clear()

    def _sync_extract(self, conversation: str):
        """同步提取（回退方案）"""
        quintuples = self.extractor.extract_quintuples(conversation)
        if quintuples:
            self.graph.store_quintuples(quintuples, source_text=conversation)

    def force_extract(self) -> Optional[dict]:
        """手动触发一次记忆提取"""
        if not self.conversation_buffer:
            return None
        conversation = "\n".join([
            f"用户: {item['user']}\nAI: {item['ai']}"
            for item in self.conversation_buffer
        ])
        quintuples = self.extractor.extract_quintuples(conversation)
        if quintuples:
            self.graph.store_quintuples(quintuples, source_text=conversation)
            return {"extracted": len(quintuples), "quintuples": quintuples}
        return {"extracted": 0}

    def get_status(self) -> dict:
        """获取 Hook 状态"""
        stats = self.graph.get_stats() if self.graph.is_connected() else {}
        return {
            "connected": self.graph.is_connected(),
            "turn_count": self.turn_count,
            "buffer_size": len(self.conversation_buffer),
            "buffer_capacity": self.buffer_size,
            "auto_extract": self.auto_extract,
            "last_recall": self._last_recall_text[:200] if self._last_recall_text else None,
            "graph_stats": stats
        }
