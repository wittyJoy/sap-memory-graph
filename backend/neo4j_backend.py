"""
Neo4j 知识图谱记忆后端
Knowledge Graph Memory Backend using Neo4j
"""
import json
import time
from datetime import datetime
from typing import Optional

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError


class MemoryGraph:
    """Neo4j 知识图谱记忆管理器"""

    def __init__(self, uri: str = "bolt://localhost:7687",
                 user: str = "neo4j", password: str = "memory123",
                 database: str = "neo4j"):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self.driver = None
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
            # 为实体创建唯一性约束
            for label in ["Person", "Place", "Object", "Event", "Concept", "Topic"]:
                try:
                    session.run(
                        f"CREATE CONSTRAINT IF NOT EXISTS "
                        f"FOR (n:{label}) REQUIRE n.name IS UNIQUE"
                    )
                except Exception:
                    pass  # 约束已存在则忽略

            # 为 Memory 节点创建索引
            try:
                session.run(
                    "CREATE INDEX IF NOT EXISTS "
                    "FOR (m:Memory) ON (m.created_at)"
                )
            except Exception:
                pass

    def is_connected(self) -> bool:
        """检查连接状态"""
        if not self.driver:
            return False
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    # ======================== 实体管理 ========================

    def upsert_entity(self, name: str, entity_type: str,
                      properties: dict = None) -> bool:
        """创建或更新实体节点"""
        if not self.driver:
            return False
        props = properties or {}
        props["name"] = name
        props["updated_at"] = datetime.now().isoformat()
        # 清理类型名，防止注入
        safe_type = "".join(c for c in entity_type if c.isalnum() or c == "_")
        if not safe_type:
            safe_type = "Concept"

        with self.driver.session(database=self.database) as session:
            session.run(
                f"MERGE (e:{safe_type} {{name: $name}}) SET e += $props",
                name=name, props=props
            )
        return True

    def upsert_relation(self, source: str, target: str,
                        relation: str, properties: dict = None) -> bool:
        """创建或更新两个实体之间的关系"""
        if not self.driver:
            return False
        props = properties or {}
        props["created_at"] = datetime.now().isoformat()
        safe_rel = "".join(c for c in relation if c.isalnum() or c == "_")
        if not safe_rel:
            safe_rel = "RELATED_TO"

        with self.driver.session(database=self.database) as session:
            session.run(
                f"""
                MATCH (a {{name: $source}})
                MATCH (b {{name: $target}})
                MERGE (a)-[r:{safe_rel}]->(b)
                SET r += $props
                """,
                source=source, target=target, props=props
            )
        return True

    # ======================== 记忆存储 ========================

    def add_memory(self, content: str, entities: list = None,
                   relations: list = None, memory_type: str = "episodic",
                   importance: float = 0.5, source: str = "conversation",
                   tags: list = None) -> Optional[int]:
        """
        添加一条记忆到知识图谱

        Args:
            content: 记忆内容摘要
            entities: [{"name": "小夜", "type": "Person"}, ...]
            relations: [{"source": "小夜", "target": "猫", "type": "likes"}, ...]
            memory_type: episodic(事件) / semantic(知识) / emotional(情感)
            importance: 0.0~1.0 重要程度
            source: 来源标识
            tags: 标签列表

        Returns:
            记忆节点 ID 或 None
        """
        if not self.driver:
            return None

        entities = entities or []
        relations = relations or []
        tags = tags or []

        with self.driver.session(database=self.database) as session:
            # 1. 创建记忆节点
            result = session.run(
                """
                CREATE (m:Memory {
                    content: $content,
                    type: $type,
                    importance: $importance,
                    source: $source,
                    tags: $tags,
                    created_at: $created_at,
                    access_count: 0,
                    last_accessed: $created_at
                })
                RETURN id(m) AS mid
                """,
                content=content, type=memory_type,
                importance=importance, source=source,
                tags=tags,
                created_at=datetime.now().isoformat()
            )
            mid = result.single()["mid"]

            # 2. 创建/关联实体
            for ent in entities:
                ent_name = ent.get("name", "").strip()
                ent_type = ent.get("type", "Concept").strip()
                if not ent_name:
                    continue
                safe_type = "".join(c for c in ent_type if c.isalnum() or c == "_") or "Concept"
                session.run(
                    f"""
                    MERGE (e:{safe_type} {{name: $name}})
                    WITH e
                    MATCH (m:Memory) WHERE id(m) = $mid
                    MERGE (m)-[:MENTIONS]->(e)
                    """,
                    name=ent_name, mid=mid
                )

            # 3. 创建实体间关系
            for rel in relations:
                src = rel.get("source", "").strip()
                tgt = rel.get("target", "").strip()
                rtype = rel.get("type", "RELATED_TO").strip()
                if not src or not tgt:
                    continue
                safe_rel = "".join(c for c in rtype if c.isalnum() or c == "_") or "RELATED_TO"
                session.run(
                    f"""
                    MATCH (a {{name: $src}})
                    MATCH (b {{name: $tgt}})
                    MERGE (a)-[r:{safe_rel}]->(b)
                    """,
                    src=src, tgt=tgt
                )

            return mid

    # ======================== 记忆召回 ========================

    def recall_by_entities(self, entities: list, limit: int = 10) -> list:
        """根据实体名召回相关记忆"""
        if not self.driver or not entities:
            return []
        with self.driver.session(database=self.database) as session:
            result = session.run(
                """
                MATCH (m:Memory)-[:MENTIONS]->(e)
                WHERE e.name IN $entities
                WITH m, COUNT(DISTINCT e) AS relevance
                ORDER BY relevance DESC, m.importance DESC, m.created_at DESC
                LIMIT $limit
                SET m.access_count = m.access_count + 1,
                    m.last_accessed = $now
                RETURN id(m) AS id, m.content AS content, m.type AS type,
                       m.importance AS importance, m.created_at AS created_at,
                       m.tags AS tags, relevance
                """,
                entities=entities, limit=limit,
                now=datetime.now().isoformat()
            )
            return [dict(record) for record in result]

    def recall_recent(self, limit: int = 20, memory_type: str = None) -> list:
        """召回最近的记忆"""
        if not self.driver:
            return []
        with self.driver.session(database=self.database) as session:
            if memory_type:
                result = session.run(
                    """
                    MATCH (m:Memory)
                    WHERE m.type = $type
                    RETURN id(m) AS id, m.content AS content, m.type AS type,
                           m.importance AS importance, m.created_at AS created_at,
                           m.tags AS tags
                    ORDER BY m.created_at DESC
                    LIMIT $limit
                    """,
                    type=memory_type, limit=limit
                )
            else:
                result = session.run(
                    """
                    MATCH (m:Memory)
                    RETURN id(m) AS id, m.content AS content, m.type AS type,
                           m.importance AS importance, m.created_at AS created_at,
                           m.tags AS tags
                    ORDER BY m.created_at DESC
                    LIMIT $limit
                    """,
                    limit=limit
                )
            return [dict(record) for record in result]

    def recall_important(self, limit: int = 10) -> list:
        """召回最重要的记忆"""
        if not self.driver:
            return []
        with self.driver.session(database=self.database) as session:
            result = session.run(
                """
                MATCH (m:Memory)
                WHERE m.importance >= 0.7
                RETURN id(m) AS id, m.content AS content, m.type AS type,
                       m.importance AS importance, m.created_at AS created_at,
                       m.tags AS tags
                ORDER BY m.importance DESC, m.access_count DESC
                LIMIT $limit
                """,
                limit=limit
            )
            return [dict(record) for record in result]

    # ======================== 图谱数据 ========================

    def get_graph_data(self, node_limit: int = 300,
                       edge_limit: int = 500) -> dict:
        """获取用于 3D 可视化的图数据"""
        if not self.driver:
            return {"nodes": [], "edges": []}

        with self.driver.session(database=self.database) as session:
            # 节点：实体 + 高重要性记忆
            nodes_result = session.run(
                """
                // 实体节点
                MATCH (e)
                WHERE NOT e:Memory AND NOT e:_BoltType
                WITH collect({
                    id: toString(id(e)),
                    name: coalesce(e.name, 'unknown'),
                    labels: labels(e),
                    type: 'entity',
                    importance: 0.5
                }) AS entities

                // 记忆节点（只取重要的）
                OPTIONAL MATCH (m:Memory)
                WHERE m.importance >= 0.4 OR m.access_count > 0
                WITH entities, collect({
                    id: toString(id(m)),
                    name: substring(m.content, 0, 30),
                    labels: ['Memory'],
                    type: 'memory',
                    importance: m.importance,
                    content: m.content,
                    mem_type: m.type,
                    created_at: m.created_at
                })[0..$mlimit] AS memories

                RETURN entities + memories AS nodes
                """,
                mlimit=node_limit // 2
            )
            nodes = nodes_result.single()["nodes"] if nodes_result.peek() else []

            # 边
            edges_result = session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE NOT a:_BoltType AND NOT b:_BoltType
                WITH a, b, type(r) AS rtype, r
                LIMIT $elimit
                RETURN toString(id(a)) AS source,
                       toString(id(b)) AS target,
                       rtype AS type
                """,
                elimit=edge_limit
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
                WITH DISTINCT n, rs
                RETURN collect(DISTINCT {{
                    id: toString(id(n)),
                    name: coalesce(n.name, substring(n.content, 0, 20)),
                    labels: labels(n),
                    importance: coalesce(n.importance, 0.5)
                }}) AS nodes,
                [] AS edges
                """,
                name=entity_name
            )
            data = result.single()
            return {"nodes": data["nodes"], "edges": data["edges"]}

    # ======================== 统计 ========================

    def get_stats(self) -> dict:
        """获取图谱统计信息"""
        if not self.driver:
            return {"connected": False}
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (m:Memory)
                WITH count(m) AS total_memories,
                     avg(m.importance) AS avg_importance,
                     max(m.created_at) AS latest_memory,
                     sum(m.access_count) AS total_access
                OPTIONAL MATCH (e)
                WHERE NOT e:Memory AND NOT e:_BoltType
                WITH total_memories, avg_importance, latest_memory,
                     total_access, count(e) AS total_entities
                OPTIONAL MATCH ()-[r]->()
                RETURN total_memories, total_entities,
                       count(r) AS total_relations,
                       avg_importance, latest_memory, total_access
            """)
            record = result.single()
            return {
                "connected": True,
                "total_memories": record["total_memories"],
                "total_entities": record["total_entities"],
                "total_relations": record["total_relations"],
                "avg_importance": round(record["avg_importance"] or 0, 2),
                "latest_memory": record["latest_memory"],
                "total_access": record["total_access"]
            }

    # ======================== 清理 ========================

    def forget_old_memories(self, days: int = 90,
                            min_importance: float = 0.3) -> int:
        """清理低重要性的旧记忆"""
        if not self.driver:
            return 0
        with self.driver.session(database=self.database) as session:
            result = session.run(
                """
                MATCH (m:Memory)
                WHERE m.importance < $min_imp
                  AND m.created_at < $cutoff
                  AND m.access_count = 0
                DETACH DELETE m
                RETURN count(m) AS deleted
                """,
                min_imp=min_importance,
                cutoff=(datetime.now().replace(
                    day=datetime.now().day - days
                ) if datetime.now().day > days else
                    datetime.now()).isoformat()
            )
            return result.single()["deleted"]

    def close(self):
        """关闭连接"""
        if self.driver:
            self.driver.close()
            self.driver = None
