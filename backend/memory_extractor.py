"""
LLM 驱动的记忆提取引擎
Extract entities, relations, and memory summaries from conversations using LLM
"""
import json
import re
from typing import Optional

try:
    import openai
except ImportError:
    openai = None


EXTRACT_SYSTEM_PROMPT = """你是一个精确的记忆提取器。从对话中提取关键信息。

规则：
1. 只提取有明确信息量的内容，忽略寒暄和无意义对话
2. 实体类型限定为：Person, Place, Object, Event, Concept, Topic, Emotion
3. 关系类型使用英文小写：likes, knows, visited, owns, works_at, lives_in, feels, wants, learned, experienced 等
4. importance 评分标准：
   - 0.9-1.0: 重大事件、核心偏好、重要决定
   - 0.7-0.8: 有意义的经历、情感表达、学习内容
   - 0.5-0.6: 日常信息、一般偏好
   - 0.3-0.4: 琐碎信息
   - 0.0-0.2: 无意义对话
5. 输出严格 JSON 格式"""

EXTRACT_USER_PROMPT = """请从以下对话中提取记忆要素：

{conversation}

输出JSON格式：
{{
    "summary": "一句话总结这条记忆的核心内容",
    "entities": [
        {{"name": "实体名", "type": "Person|Place|Object|Event|Concept|Topic|Emotion"}}
    ],
    "relations": [
        {{"source": "实体A", "target": "实体B", "type": "关系类型"}}
    ],
    "importance": 0.0到1.0的浮点数,
    "memory_type": "episodic|semantic|emotional",
    "tags": ["标签1", "标签2"]
}}"""

QUICK_ENTITY_PROMPT = """从以下文本中识别人名、地名、物品名、事件名、概念名。
只输出JSON数组，不要其他文字。
如果没有识别到任何实体，输出空数组 []。

文本：{text}

输出：["实体1", "实体2"]"""


class MemoryExtractor:
    """使用 LLM 从对话中提取记忆要素"""

    def __init__(self, api_key: str = None, base_url: str = None,
                 model: str = "deepseek-chat"):
        self.model = model
        self.client = None
        if openai and api_key:
            self.client = openai.OpenAI(
                api_key=api_key,
                base_url=base_url
            )

    def extract_memory(self, conversation: str) -> Optional[dict]:
        """
        从一段对话中提取记忆要素

        Args:
            conversation: 对话文本，格式 "用户: xxx\nAI: xxx"

        Returns:
            {
                "summary": "...",
                "entities": [...],
                "relations": [...],
                "importance": 0.7,
                "memory_type": "episodic",
                "tags": [...]
            }
        """
        if not self.client:
            return self._fallback_extract(conversation)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": EXTRACT_USER_PROMPT.format(
                        conversation=conversation
                    )}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)

            # 校验和清理
            return self._validate_extract(data)
        except Exception as e:
            print(f"[MemoryExtractor] 提取失败: {e}")
            return self._fallback_extract(conversation)

    def quick_extract_entities(self, text: str) -> list:
        """快速从文本中提取实体名列表"""
        if not self.client:
            return self._fallback_entities(text)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": QUICK_ENTITY_PROMPT.format(
                        text=text
                    )}
                ],
                temperature=0,
                max_tokens=200
            )
            raw = response.choices[0].message.content.strip()
            # 清理可能的 markdown 代码块
            raw = re.sub(r'```json?\s*', '', raw)
            raw = re.sub(r'```', '', raw)
            entities = json.loads(raw)
            if isinstance(entities, list):
                return [str(e) for e in entities if e]
            return []
        except Exception as e:
            print(f"[MemoryExtractor] 实体提取失败: {e}")
            return self._fallback_entities(text)

    def _validate_extract(self, data: dict) -> dict:
        """校验和清理提取结果"""
        result = {
            "summary": data.get("summary", ""),
            "entities": [],
            "relations": [],
            "importance": max(0.0, min(1.0, float(data.get("importance", 0.5)))),
            "memory_type": data.get("memory_type", "episodic"),
            "tags": data.get("tags", [])
        }

        # 清理实体
        valid_types = {"Person", "Place", "Object", "Event",
                       "Concept", "Topic", "Emotion"}
        for ent in data.get("entities", []):
            name = ent.get("name", "").strip()
            etype = ent.get("type", "Concept").strip()
            if name and len(name) < 50:
                if etype not in valid_types:
                    etype = "Concept"
                result["entities"].append({"name": name, "type": etype})

        # 清理关系
        for rel in data.get("relations", []):
            src = rel.get("source", "").strip()
            tgt = rel.get("target", "").strip()
            rtype = rel.get("type", "related_to").strip().lower()
            if src and tgt and src != tgt:
                result["relations"].append({
                    "source": src, "target": tgt, "type": rtype
                })

        return result

    def _fallback_extract(self, conversation: str) -> dict:
        """LLM 不可用时的回退提取（基于规则）"""
        entities = self._fallback_entities(conversation)
        return {
            "summary": conversation[:100] + "..." if len(conversation) > 100 else conversation,
            "entities": [{"name": e, "type": "Concept"} for e in entities],
            "relations": [],
            "importance": 0.5,
            "memory_type": "episodic",
            "tags": []
        }

    def _fallback_entities(self, text: str) -> list:
        """基于 jieba 的简单实体提取"""
        try:
            import jieba.posseg as pseg
            words = pseg.cut(text)
            entities = []
            for word, flag in words:
                if flag.startswith('nr') and len(word) >= 2:  # 人名
                    entities.append(word)
                elif flag.startswith('ns') and len(word) >= 2:  # 地名
                    entities.append(word)
                elif flag.startswith('nz') and len(word) >= 2:  # 其他专名
                    entities.append(word)
            return list(set(entities))[:10]
        except ImportError:
            # jieba 不可用，用最简单的中文人名匹配
            import re
            # 匹配2-4个中文字符的人名模式
            names = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
            # 过滤常见非人名
            stop_words = {'你好', '什么', '怎么', '可以', '这个', '那个',
                          '就是', '但是', '因为', '所以', '如果', '已经'}
            return [n for n in set(names) if n not in stop_words][:10]
