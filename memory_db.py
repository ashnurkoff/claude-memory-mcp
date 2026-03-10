"""
Persistent memory storage layer using Upstash Redis (REST API).
Supports namespaces: global (shared) + per-project isolation.

Env vars:
  UPSTASH_REDIS_REST_URL   — Upstash REST endpoint
  UPSTASH_REDIS_REST_TOKEN — Upstash REST token
  MEMORY_NAMESPACE         — project namespace (default: "global")
"""

import json
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional

from upstash_redis import Redis


@dataclass
class Memory:
    id: int
    namespace: str
    content: str
    summary: str
    entities: list[str]
    topics: list[str]
    importance: float
    source: str
    memory_type: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class MemoryDB:
    """Upstash Redis memory store with namespace support.

    Key schema:
      mem:{ns}:seq              — auto-increment counter
      mem:{ns}:data:{id}        — JSON blob per memory
      mem:{ns}:timeline         — sorted set (score=timestamp, member=id)
    """

    def __init__(
        self,
        redis: Redis | None = None,
        namespace: str | None = None,
    ):
        self.redis = redis or Redis.from_env()
        self.namespace = namespace or os.environ.get("MEMORY_NAMESPACE", "global")

    def _key(self, *parts: str) -> str:
        return ":".join(["mem", self.namespace, *parts])

    def _global_key(self, *parts: str) -> str:
        return ":".join(["mem", "global", *parts])

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _now_ts(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def _dict_to_memory(self, data: dict) -> Memory:
        return Memory(
            id=data["id"],
            namespace=data.get("namespace", self.namespace),
            content=data["content"],
            summary=data.get("summary", ""),
            entities=data.get("entities", []),
            topics=data.get("topics", []),
            importance=data.get("importance", 0.5),
            source=data.get("source", "manual"),
            memory_type=data.get("memory_type", "fact"),
            created_at=data.get("created_at", ""),
        )

    # ── Write ─────────────────────────────────────────────

    def add(
        self,
        content: str,
        summary: str = "",
        entities: list[str] | None = None,
        topics: list[str] | None = None,
        importance: float = 0.5,
        source: str = "manual",
        memory_type: str = "fact",
        to_global: bool = False,
    ) -> Memory:
        """Add a memory. Set to_global=True to write to shared namespace."""
        ns = "global" if to_global else self.namespace
        seq_key = ":".join(["mem", ns, "seq"])
        timeline_key = ":".join(["mem", ns, "timeline"])

        new_id = self.redis.incr(seq_key)
        now = self._now()
        now_ts = self._now_ts()

        data = {
            "id": new_id,
            "namespace": ns,
            "content": content,
            "summary": summary or content[:200],
            "entities": entities or [],
            "topics": topics or [],
            "importance": importance,
            "source": source,
            "memory_type": memory_type,
            "created_at": now,
        }

        data_key = ":".join(["mem", ns, "data", str(new_id)])
        pipe = self.redis.pipeline()
        pipe.set(data_key, json.dumps(data))
        pipe.zadd(timeline_key, {str(new_id): now_ts})
        pipe.exec()

        return self._dict_to_memory(data)

    # ── Read ──────────────────────────────────────────────

    def get(self, memory_id: int, namespace: str | None = None) -> Optional[Memory]:
        ns = namespace or self.namespace
        raw = self.redis.get(":".join(["mem", ns, "data", str(memory_id)]))
        if raw is None:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        return self._dict_to_memory(data)

    def _list_ns(
        self,
        ns: str,
        limit: int = 50,
        offset: int = 0,
        memory_type: str | None = None,
    ) -> list[Memory]:
        """List memories from a single namespace."""
        timeline_key = ":".join(["mem", ns, "timeline"])

        # Get IDs from timeline (newest first)
        # Fetch more than needed to account for type filtering
        fetch_count = (limit + offset) * 3 if memory_type else (limit + offset)
        ids = self.redis.zrange(timeline_key, 0, fetch_count - 1, rev=True)

        if not ids:
            return []

        # Batch fetch memory data
        keys = [":".join(["mem", ns, "data", str(mid)]) for mid in ids]
        raw_list = self.redis.mget(*keys)

        memories = []
        for raw in raw_list:
            if raw is None:
                continue
            data = json.loads(raw) if isinstance(raw, str) else raw
            if memory_type and data.get("memory_type") != memory_type:
                continue
            memories.append(self._dict_to_memory(data))

        return memories[offset : offset + limit]

    def list_all(
        self,
        limit: int = 50,
        offset: int = 0,
        memory_type: str | None = None,
        scope: str = "both",
    ) -> list[Memory]:
        """List memories.

        scope: "project" | "global" | "both"
        When "both", merges project + global, sorted by created_at desc.
        """
        if scope == "project" or (scope == "both" and self.namespace == "global"):
            return self._list_ns(self.namespace, limit, offset, memory_type)

        if scope == "global":
            return self._list_ns("global", limit, offset, memory_type)

        # both: merge project + global
        project_mems = self._list_ns(self.namespace, limit=200, memory_type=memory_type)
        global_mems = self._list_ns("global", limit=200, memory_type=memory_type)

        # Deduplicate (global memories won't collide with project — different namespace)
        combined = project_mems + global_mems
        combined.sort(key=lambda m: m.created_at, reverse=True)
        return combined[offset : offset + limit]

    def search(
        self,
        query: str,
        limit: int = 20,
        scope: str = "both",
    ) -> list[Memory]:
        """Keyword search across content, summary, entities, topics."""
        q_lower = query.lower()
        all_mems = self.list_all(limit=500, scope=scope)

        results = []
        for m in all_mems:
            searchable = " ".join([
                m.content,
                m.summary,
                " ".join(m.entities),
                " ".join(m.topics),
            ]).lower()
            if q_lower in searchable:
                results.append(m)

        # Sort by importance desc
        results.sort(key=lambda m: m.importance, reverse=True)
        return results[:limit]

    # ── Delete ────────────────────────────────────────────

    def delete(self, memory_id: int, namespace: str | None = None) -> bool:
        ns = namespace or self.namespace
        data_key = ":".join(["mem", ns, "data", str(memory_id)])
        timeline_key = ":".join(["mem", ns, "timeline"])

        existed = self.redis.exists(data_key)
        if existed:
            pipe = self.redis.pipeline()
            pipe.delete(data_key)
            pipe.zrem(timeline_key, str(memory_id))
            pipe.exec()
        return bool(existed)

    def clear(self, namespace: str | None = None) -> int:
        """Clear all memories in a namespace."""
        ns = namespace or self.namespace
        timeline_key = ":".join(["mem", ns, "timeline"])
        ids = self.redis.zrange(timeline_key, 0, -1)

        if not ids:
            return 0

        keys_to_delete = [":".join(["mem", ns, "data", str(mid)]) for mid in ids]
        keys_to_delete.append(timeline_key)
        keys_to_delete.append(":".join(["mem", ns, "seq"]))

        self.redis.delete(*keys_to_delete)
        return len(ids)

    # ── Stats ─────────────────────────────────────────────

    def stats(self, scope: str = "both") -> dict:
        project_count = self.redis.zcard(self._key("timeline"))
        result = {
            "namespace": self.namespace,
            "project_memories": project_count,
        }

        if self.namespace != "global":
            global_count = self.redis.zcard(self._global_key("timeline"))
            result["global_memories"] = global_count
            result["total"] = project_count + global_count
        else:
            result["total"] = project_count

        # Type breakdown (sample from list)
        if scope in ("both", "project"):
            all_mems = self.list_all(limit=500, scope=scope)
            by_type: dict[str, int] = {}
            for m in all_mems:
                by_type[m.memory_type] = by_type.get(m.memory_type, 0) + 1
            result["by_type"] = by_type

        return result
