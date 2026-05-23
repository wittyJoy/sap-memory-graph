"""
Neo4j 知识图谱记忆后端 (GRAG 架构)
基于 NagaAgent 五元组模式改造

五元组结构：(主体, 主体类型, 谓词, 客体, 客体类型)
双重存储：Neo4j + 本地 JSON 备份
"""
import json
import hashlib
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError


# ======================== 实体类型定义 ========================
ENTITY_TYPES = {
    "person", "location", "organization", "item",
    "concept", "time", "event", "activity"
}


class MemoryGraph:
    """Neo4j 知识图谱记忆管理器（GRAG 五元组架构）"""

    def __init__(self, uri: str = "bolt://localhost:7687",
                 user: str = "neo4j", password: str = "memory123",
                 database: str = "neo4j",
                 local_backup_dir: str = "logs/knowledge_graph"):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self.driver = None

        # 本地 JSON 备份路径
        self.backup_dir = Path(local_backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.quintuples_file = self.backup_dir / "quintuples.json"
        self._local_quintuples = self._load_local_quintuples()

        # SHA-256 去重集合
        self._processed_hashes: set = set()

        # 异步任务队列
        self._task_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._workers_started = False

        self._connect()

    def _connect(self):
        """建立 Neo4j 连接"""
        try:
            self.driver = GraphDatabase.driver(
                self.uri, auth=(self.user, self.password),
                max_connection_lifetime=3600
            )
            self.driver.verify_connectivity()
            self._init_constraints()
            print(f"[MemoryGraph] ✅ Neo4j 连接成功: {self.uri}")
        except (ServiceUnavailable, AuthError) as e:
            print(f"[MemoryGraph] ❌ Neo4j 连接失败: {e}")
            self.driver = None

    def _init_constraints(self):
        """初始化数据库约束和索引"""
        if not self.driver:
            return
        with self.driver.session(database=self.database) as session:
            for label in ["person", "location", "organization", "item",
                          "concept", "time", "event", "activity"]:
                try:
                    session.run(
                        f"CREATE CONSTRAINT IF NOT EXISTS "
                        f"FOR (n:{label}) REQUIRE n.name IS UNIQUE"
                    )
                except Exception:
                    pass
            try:
                session.run(
                    "CREATE INDEX IF NOT EXISTS "
                    "FOR (q:Quintuple) ON (q.hash)"
                )
            except Exception:
                pass

    def is_connected(self) -> bool:
        """检查 Neo4j 驱动是否可用"""
        if not self.driver:
            return False
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    # ======================== 本地备份 ========================

    def _load_local_quintuples(self) -> list:
        """从本地 JSON 加载历史五元组"""
        if self.quintuples_file.exists():
            try:
                return json.loads(
                    self.quintuples_file.read_text(encoding="utf-8")
                )
            except Exception:
                return []
        return []

    def _save_local_quintuples(self):
        """保存五元组到本地 JSON"""
        try:
            self.quintuples_file.write_text(
                json.dumps(self._local_quintuples, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[MemoryGraph] 本地备份保存失败: {e}")

    # ======================== SHA-256 去重 ========================

    @staticmethod
    def _compute_hash(text: str) -> str:
        """计算文本的 SHA-256 哈希"""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def is_duplicate(self, text: str) -> bool:
        """检查文本是否已处理过"""
        h = self._compute_hash(text)
        if h in self._processed_hashes:
            return True
        self._processed_hashes.add(h)
        return False

    # ======================== 五元组存储 ========================

    def store_quintuples(self, quintuples: list, source_text: str = "") -> list:
        """
        存储五元组到 Neo4j + 本地 JSON

        Args:
            quintuples: [{
                "subject": "小夜",
                "subject_type": "person",
                "predicate": "喜欢",
                "object": "猫",
                "object_type": "item"
            }, ...]
            source_text: 原始对话文本（用于去重）

        Returns:
            存储成功的五元组 ID 列表
        """
        if not quintuples:
            return []

        # SHA-256 去重：同一段对话文本不重复提取
        if source_text and self.is_duplicate(source_text):
            print("[MemoryGraph] ⏭️ 重复文本，跳过")
            return []

        stored_ids = []

        for q in quintuples:
            subj = q.get("subject", "").strip()
            subj_type = q.get("subject_type", "concept").strip().lower()
            predicate = q.get("predicate", "").strip()
            obj = q.get("object", "").strip()
            obj_type = q.get("object_type", "concept").strip().lower()

            if not subj or not obj or not predicate:
                continue

            # 校验类型
            if subj_type not in ENTITY_TYPES:
                subj_type = "concept"
            if obj_type not in ENTITY_TYPES:
                obj_type = "concept"

            # 计算五元组哈希（去重）
            q_hash = self._compute_hash(
                f"{subj}|{subj_type}|{predicate}|{obj}|{obj_type}"
            )

            now = datetime.now().isoformat()

            # 1. 存入 Neo4j：实体 MERGE + QUINTUPLE 关系 MERGE（谓词存于关系属性）
            if self.driver:
                try:
                    with self.driver.session(database=self.database) as session:
                        result = session.run(
                            f"""
                            MERGE (a:{subj_type} {{name: $subj}})
                            MERGE (b:{obj_type} {{name: $obj}})
                            MERGE (a)-[r:QUINTUPLE {{
                                predicate: $predicate,
                                hash: $q_hash
                            }}]->(b)
                            SET r.created_at = $now,
                                r.source = $source,
                                r.access_count = coalesce(r.access_count, 0)
                            RETURN id(r) AS rid
                            """,
                            subj=subj, obj=obj, predicate=predicate,
                            q_hash=q_hash, now=now, source="conversation"
                        )
                        rid = result.single()["rid"]
                        stored_ids.append(rid)
                except Exception as e:
                    print(f"[MemoryGraph] Neo4j 存储失败: {e}")

            # 2. 存入本地 JSON 备份（Neo4j 不可用时仍可恢复）
            entry = {
                "subject": subj,
                "subject_type": subj_type,
                "predicate": predicate,
                "object": obj,
                "object_type": obj_type,
                "hash": q_hash,
                "created_at": now,
                "source": "conversation"
            }
            # 检查本地是否已存在
            existing_hashes = {q.get("hash") for q in self._local_quintuples}
            if q_hash not in existing_hashes:
                self._local_quintuples.append(entry)

        # 持久化本地备份
        self._save_local_quintuples()

        return stored_ids

    # ======================== RAG 检索 ========================

    def rag_retrieve(self, keywords: list, limit: int = 10) -> str:
        """
        基于关键词的 RAG 检索，返回格式化的记忆文本

        流程：关键词提取 → Cypher 查询 → 格式化注入上下文

        Args:
            keywords: 从用户消息中提取的关键词列表
            limit: 最大返回条数

        Returns:
            格式化的记忆文本，如：
            "小夜(person) —[喜欢]→ 猫(item)\n用户(person) —[住在]→ 北京(location)"
        """
        if not self.driver or not keywords:
            return ""

        with self.driver.session(database=self.database) as session:
            result = session.run(
                """
                MATCH (a)-[r:QUINTUPLE]->(b)
                WHERE a.name IN $keywords OR b.name IN $keywords
                WITH a, r, b,
                     CASE WHEN a.name IN $keywords THEN 2 ELSE 1 END +
                     CASE WHEN b.name IN $keywords THEN 2 ELSE 1 END AS score
                ORDER BY score DESC, r.access_count DESC
                LIMIT $limit
                SET r.access_count = coalesce(r.access_count, 0) + 1
                RETURN labels(a)[0] AS a_type, a.name AS a_name,
                       r.predicate AS predicate,
                       labels(b)[0] AS b_type, b.name AS b_name
                """,
                keywords=keywords, limit=limit
            )
            # 召回时递增 access_count，供 recall_important 排序使用

            lines = []
            for record in result:
                a_type = record["a_type"] or "concept"
                a_name = record["a_name"]
                pred = record["predicate"]
                b_type = record["b_type"] or "concept"
                b_name = record["b_name"]
                lines.append(
                    f"{a_name}({a_type}) —[{pred}]→ {b_name}({b_type})"
                )

            return "\n".join(lines)

    def recall_recent(self, limit: int = 20) -> list:
        """召回最近的五元组"""
        if not self.driver:
            return []
        with self.driver.session(database=self.database) as session:
            result = session.run(
                """
                MATCH (a)-[r:QUINTUPLE]->(b)
                RETURN labels(a)[0] AS a_type, a.name AS a_name,
                       r.predicate AS predicate,
                       labels(b)[0] AS b_type, b.name AS b_name,
                       r.created_at AS created_at
                ORDER BY r.created_at DESC
                LIMIT $limit
                """,
                limit=limit
            )
            return [dict(record) for record in result]

    def recall_important(self, limit: int = 10) -> list:
        """召回访问次数最多的五元组（最常被引用）"""
        if not self.driver:
            return []
        with self.driver.session(database=self.database) as session:
            result = session.run(
                """
                MATCH (a)-[r:QUINTUPLE]->(b)
                WHERE coalesce(r.access_count, 0) > 0
                RETURN labels(a)[0] AS a_type, a.name AS a_name,
                       r.predicate AS predicate,
                       labels(b)[0] AS b_type, b.name AS b_name,
                       r.access_count AS access_count,
                       r.created_at AS created_at
                ORDER BY r.access_count DESC
                LIMIT $limit
                """,
                limit=limit
            )
            return [dict(record) for record in result]

    # ======================== 图谱数据（3D 可视化）========================

    def get_graph_data(self, node_limit: int = 100,
                       edge_limit: int = 200) -> dict:
        """
        获取用于 3D 可视化的图数据

        映射规则：
        - subject/object → 节点
        - predicate → 有向边
        - 度中心性 → 节点高度权重
        """
        if not self.driver:
            return {"nodes": [], "edges": []}

        with self.driver.session(database=self.database) as session:
            # 获取节点及其度中心性
            nodes_result = session.run(
                """
                MATCH (n)
                WHERE NOT n:_BoltType
                  AND any(label IN labels(n) WHERE label IN $entity_types)
                WITH n, size([(n)--() | 1]) AS degree
                ORDER BY degree DESC
                LIMIT $limit
                RETURN toString(id(n)) AS id,
                       n.name AS name,
                       labels(n)[0] AS label,
                       degree
                """,
                entity_types=list(ENTITY_TYPES),
                limit=node_limit
            )
            nodes = [dict(record) for record in nodes_result]

            # 获取边
            edges_result = session.run(
                """
                MATCH (a)-[r:QUINTUPLE]->(b)
                RETURN toString(id(a)) AS source,
                       toString(id(b)) AS target,
                       r.predicate AS predicate
                LIMIT $limit
                """,
                limit=edge_limit
            )
            edges = [dict(record) for record in edges_result]

            return {"nodes": nodes, "edges": edges}

    def get_entity_neighbors(self, entity_name: str,
                             depth: int = 2) -> dict:
        """获取某个实体的邻居子图"""
        if not self.driver:
            return {"nodes": [], "edges": []}
        with self.driver.session(database=self.database) as session:
            result = session.run(
                f"""
                MATCH path = (center {{name: $name}})-[*1..{depth}]-(neighbor)
                WITH nodes(path) AS ns, relationships(path) AS rs
                UNWIND ns AS n
                WITH DISTINCT n
                WHERE NOT n:_BoltType
                RETURN collect(DISTINCT {{
                    id: toString(id(n)),
                    name: n.name,
                    label: labels(n)[0]
                }}) AS nodes
                """,
                name=entity_name
            )
            data = result.single()
            return {"nodes": data["nodes"], "edges": []}

    # ======================== 统计 ========================

    def get_stats(self) -> dict:
        """返回图谱统计：五元组总数、实体数、累计召回次数、本地备份数"""
        if not self.driver:
            return {"connected": False, "local_count": len(self._local_quintuples)}
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH ()-[r:QUINTUPLE]->()
                WITH count(r) AS total_quintuples,
                     sum(coalesce(r.access_count, 0)) AS total_access
                MATCH (n)
                WHERE NOT n:_BoltType
                  AND any(label IN labels(n) WHERE label IN $entity_types)
                RETURN total_quintuples, count(n) AS total_entities, total_access
            """, entity_types=list(ENTITY_TYPES))
            record = result.single()
            return {
                "connected": True,
                "total_quintuples": record["total_quintuples"],
                "total_entities": record["total_entities"],
                "total_access": record["total_access"],
                "local_backup_count": len(self._local_quintuples)
            }

    # ======================== 异步任务管理器 ========================

    async def start_workers(self, num_workers: int = 3):
        """启动异步 worker 消费记忆提取任务"""
        if self._workers_started:
            return
        self._workers_started = True
        for i in range(num_workers):
            asyncio.create_task(self._worker(f"worker-{i}"))
        print(f"[MemoryGraph] ⚙️ {num_workers} 个 worker 已启动")

    async def _worker(self, name: str):
        """异步 worker：从队列中消费任务"""
        while True:
            try:
                task = await self._task_queue.get()
                if task is None:  # 停止信号
                    break
                await self._process_task(task, name)
                self._task_queue.task_done()
            except Exception as e:
                print(f"[{name}] 任务处理异常: {e}")

    async def _process_task(self, task: dict, worker_name: str):
        """处理单个记忆提取任务"""
        text = task.get("text", "")
        extractor = task.get("extractor")
        if not text or not extractor:
            return

        try:
            # 在线程池中运行同步的 LLM 调用
            loop = asyncio.get_event_loop()
            quintuples = await loop.run_in_executor(
                None, extractor.extract_quintuples, text
            )
            if quintuples:
                self.store_quintuples(quintuples, source_text=text)
                print(f"[{worker_name}] ✅ 存储 {len(quintuples)} 个五元组")
        except Exception as e:
            print(f"[{worker_name}] 提取失败: {e}")

    async def enqueue_task(self, text: str, extractor):
        """将记忆提取任务加入队列"""
        if self._task_queue.full():
            print("[MemoryGraph] ⚠️ 任务队列已满，丢弃")
            return False
        await self._task_queue.put({"text": text, "extractor": extractor})
        return True

    # ======================== 清理 ========================

    def forget_old_quintuples(self, days: int = 90, min_access: int = 0) -> int:
        """
        删除超过指定天数且从未被 RAG 召回的五元组关系

        按 access_count 判断价值，删除长期未被 RAG 召回的关系。
        孤立实体节点（无任何关系）会在关系删除后一并清除。
        """
        if not self.driver:
            return 0
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self.driver.session(database=self.database) as session:
            result = session.run(
                """
                MATCH ()-[r:QUINTUPLE]->()
                WHERE coalesce(r.access_count, 0) <= $min_access
                  AND r.created_at < $cutoff
                DELETE r
                RETURN count(r) AS deleted
                """,
                min_access=min_access,
                cutoff=cutoff,
            )
            deleted = result.single()["deleted"]
            # 清理无关系的孤立实体节点
            session.run(
                """
                MATCH (n)
                WHERE NOT n:_BoltType
                  AND any(label IN labels(n) WHERE label IN $entity_types)
                  AND NOT (n)--()
                DELETE n
                """,
                entity_types=list(ENTITY_TYPES),
            )
            return deleted

    async def cleanup_old_tasks(self, hours: int = 24):
        """定期清理（预留接口，后续可清理队列或本地备份）"""
        pass

    def close(self):
        """关闭 Neo4j 驱动连接"""
        if self.driver:
            self.driver.close()
            self.driver = None
