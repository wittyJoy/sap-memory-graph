"""
五元组记忆提取引擎 (GRAG 架构)
基于 NagaAgent 的 QuintupleResponse 模式改造

五元组：(主体, 主体类型, 谓词, 客体, 客体类型)
提取方式：结构化 Pydantic 解析 → JSON 兜底 → 规则过滤
"""
import json
import re
from typing import Optional, List
from pydantic import BaseModel, Field

try:
    import openai
except ImportError:
    openai = None


# ======================== Pydantic 模型 ========================

class Quintuple(BaseModel):
    """单个五元组"""
    subject: str = Field(description="主体名称")
    subject_type: str = Field(
        description="主体类型",
        pattern="^(person|location|organization|item|concept|time|event|activity)$"
    )
    predicate: str = Field(description="谓词/关系")
    object: str = Field(description="客体名称")
    object_type: str = Field(
        description="客体类型",
        pattern="^(person|location|organization|item|concept|time|event|activity)$"
    )


class QuintupleResponse(BaseModel):
    """LLM 结构化输出的响应模型"""
    quintuples: List[Quintuple] = Field(
        description="从对话中提取的五元组列表",
        default_factory=list
    )
    summary: str = Field(
        description="对话内容的一句话总结",
        default=""
    )
    importance: float = Field(
        description="重要程度 0.0-1.0",
        ge=0.0, le=1.0,
        default=0.5
    )
    tags: List[str] = Field(
        description="标签列表",
        default_factory=list
    )


# ======================== Prompt 模板 ========================

STRUCTURED_SYSTEM_PROMPT = """你是一个精确的记忆提取器。从对话中提取知识五元组。

## 五元组结构
(主体, 主体类型, 谓词, 客体, 客体类型)

## 实体类型（严格限定）
- person: 人名、角色
- location: 地点、地址
- organization: 组织、公司
- item: 物品、产品
- concept: 概念、想法、主题
- time: 时间、日期
- event: 事件
- activity: 活动、行为

## 谓词规范
使用简洁的中文动词或短语：喜欢、住在、拥有、工作于、知道、去过、想要、感觉、学会、经历、属于、包含、讨厌、害怕、擅长...

## 过滤规则（重要！）
只提取以下类型的事实：
1. **行为**：做了什么、正在做什么
2. **关系**：人与人、人与物的关系
3. **状态**：当前状态、属性
4. **偏好**：喜欢/讨厌什么
5. **经历**：去过哪里、做过什么

过滤掉：
- 隐喻、比喻
- 假设、猜测
- 纯情感表达（无实质信息）
- 寒暄、客套话"""

STRUCTURED_USER_PROMPT = """请从以下对话中提取五元组：

{conversation}

以 JSON 格式输出，包含 quintuples、summary、importance、tags 字段。"""


# ======================== 事实过滤关键词 ========================
# FACT_INDICATORS 预留：可用于谓词级别的规则过滤（当前由 LLM Prompt 承担）
FACT_INDICATORS = {
    "是", "有", "在", "住", "喜欢", "讨厌", "拥有", "知道",
    "去过", "想要", "觉得", "认为", "学过", "做过", "吃",
    "买", "看", "听", "玩", "用", "属于", "包含", "认识",
    "工作", "学习", "生活", "出生", "住在", "来自"
}
NON_FACT_PATTERNS = [
    r"^如果", r"^假如", r"^要是", r"^假设",  # 假设
    r"好像", r"似乎", r"可能", r"大概",       # 不确定
    r"哈哈", r"嘻嘻", r"呜呜", r"哇",         # 纯情感
    r"^你好", r"^谢谢", r"^不客气", r"^对不起" # 寒暄
]


class MemoryExtractor:
    """使用 LLM 从对话中提取五元组记忆"""

    def __init__(self, api_key: str = None, base_url: str = None,
                 model: str = "deepseek-chat"):
        self.model = model
        self.client = None
        if openai and api_key:
            self.client = openai.OpenAI(
                api_key=api_key,
                base_url=base_url
            )

    def extract_quintuples(self, conversation: str) -> list:
        """
        从对话中提取五元组（主方法）

        优先使用结构化 Pydantic 解析，失败则 JSON 兜底

        Args:
            conversation: 对话文本

        Returns:
            [{
                "subject": "小夜",
                "subject_type": "person",
                "predicate": "喜欢",
                "object": "猫",
                "object_type": "item"
            }, ...]
        """
        if not self.client:
            return self._fallback_extract(conversation)

        # 尝试 1：结构化解析（重试 3 次）
        for attempt in range(3):
            try:
                result = self._structured_extract(conversation)
                if result:
                    return result
            except Exception as e:
                print(f"[MemoryExtractor] 结构化提取 attempt {attempt+1} 失败: {e}")

        # 尝试 2：JSON 兜底
        try:
            return self._json_fallback_extract(conversation)
        except Exception as e:
            print(f"[MemoryExtractor] JSON 兜底也失败: {e}")
            return self._fallback_extract(conversation)

    def _structured_extract(self, conversation: str) -> Optional[list]:
        """使用 Pydantic 结构化解析提取五元组"""
        response = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": STRUCTURED_SYSTEM_PROMPT},
                {"role": "user", "content": STRUCTURED_USER_PROMPT.format(
                    conversation=conversation
                )}
            ],
            temperature=0.3,
            response_format=QuintupleResponse
        )
        parsed = response.choices[0].message.parsed
        if not parsed or not parsed.quintuples:
            return None

        # 过滤
        filtered = []
        for q in parsed.quintuples:
            if self._is_valid_quintuple(q):
                filtered.append({
                    "subject": q.subject.strip(),
                    "subject_type": q.subject_type.strip().lower(),
                    "predicate": q.predicate.strip(),
                    "object": q.object.strip(),
                    "object_type": q.object_type.strip().lower()
                })
        return filtered

    def _json_fallback_extract(self, conversation: str) -> list:
        """JSON 兜底：提取首个 [ 到末尾 ] 的内容"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": STRUCTURED_SYSTEM_PROMPT},
                {"role": "user", "content": STRUCTURED_USER_PROMPT.format(
                    conversation=conversation
                )}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        raw = response.choices[0].message.content.strip()

        # 提取 JSON 数组
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            data = json.loads(raw).get("quintuples", [])

        filtered = []
        for q in data:
            if isinstance(q, dict) and q.get("subject") and q.get("object"):
                subj_type = q.get("subject_type", "concept").lower()
                obj_type = q.get("object_type", "concept").lower()
                if subj_type not in {"person", "location", "organization",
                                     "item", "concept", "time", "event", "activity"}:
                    subj_type = "concept"
                if obj_type not in {"person", "location", "organization",
                                    "item", "concept", "time", "event", "activity"}:
                    obj_type = "concept"
                filtered.append({
                    "subject": q["subject"].strip(),
                    "subject_type": subj_type,
                    "predicate": q.get("predicate", "related_to").strip(),
                    "object": q["object"].strip(),
                    "object_type": obj_type
                })
        return filtered

    def quick_extract_keywords(self, text: str) -> list:
        """快速从文本中提取关键词（用于 RAG 检索）"""
        if not self.client:
            return self._fallback_keywords(text)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": (
                        "从以下文本中提取关键词（人名、地名、物品名、关键概念），"
                        "输出JSON数组。没有则输出 []。"
                        f"\n\n文本：{text}\n\n输出："
                    )
                }],
                temperature=0,
                max_tokens=200
            )
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r'```json?\s*', '', raw)
            raw = re.sub(r'```', '', raw)
            keywords = json.loads(raw)
            if isinstance(keywords, list):
                return [str(k) for k in keywords if k][:10]
            return []
        except Exception:
            return self._fallback_keywords(text)

    def _is_valid_quintuple(self, q) -> bool:
        """校验五元组是否有效（事实过滤）"""
        subj = q.subject.strip() if q.subject else ""
        pred = q.predicate.strip() if q.predicate else ""
        obj = q.object.strip() if q.object else ""

        if not subj or not pred or not obj:
            return False
        if len(subj) > 50 or len(obj) > 50:
            return False
        if subj == obj:
            return False

        # 过滤非事实模式
        combined = f"{subj}{pred}{obj}"
        for pattern in NON_FACT_PATTERNS:
            if re.search(pattern, combined):
                return False

        return True

    def _fallback_extract(self, conversation: str) -> list:
        """LLM 不可用时的回退提取"""
        keywords = self._fallback_keywords(conversation)
        if len(keywords) >= 2:
            return [{
                "subject": keywords[0],
                "subject_type": "concept",
                "predicate": "关联",
                "object": keywords[1],
                "object_type": "concept"
            }]
        return []

    def _fallback_keywords(self, text: str) -> list:
        """基于 jieba 的关键词提取"""
        try:
            import jieba.posseg as pseg
            words = pseg.cut(text)
            keywords = []
            for word, flag in words:
                if len(word) >= 2 and (
                    flag.startswith('nr') or   # 人名
                    flag.startswith('ns') or   # 地名
                    flag.startswith('nz') or   # 专有名词
                    flag.startswith('n')       # 名词
                ):
                    keywords.append(word)
            return list(set(keywords))[:10]
        except ImportError:
            import re
            names = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
            stop_words = {'你好', '什么', '怎么', '可以', '这个', '那个',
                          '就是', '但是', '因为', '所以', '如果', '已经'}
            return [n for n in set(names) if n not in stop_words][:10]
