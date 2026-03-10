"""
Always-On Memory MCP Server for Claude Code — Upstash Redis + Namespaces.

Features:
  - Upstash Redis REST API (works from anywhere, no local Redis needed)
  - Dual namespace: global (shared) + per-project (isolated)
  - Hybrid LLM: Haiku-enhanced when API key present, storage-only otherwise
  - 10 tools for full memory lifecycle

Env vars:
  UPSTASH_REDIS_REST_URL    — Upstash REST endpoint (required)
  UPSTASH_REDIS_REST_TOKEN  — Upstash REST token (required)
  MEMORY_NAMESPACE          — project namespace (default: "global")
  ANTHROPIC_API_KEY         — enables Haiku-enhanced mode (optional)
  MEMORY_MODEL              — LLM model override (optional)

Setup:
  pip install -r requirements.txt
  claude mcp add memory -- python /path/to/server.py
"""

import json
import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field, ConfigDict

from memory_db import MemoryDB
import memory_llm as llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory-mcp")


# ── Namespace detection ───────────────────────────────────

import re

def _detect_namespace() -> str:
    """Auto-detect namespace: explicit env var > CWD-based > 'global'."""
    explicit = os.environ.get("MEMORY_NAMESPACE")
    if explicit:
        return explicit

    cwd = os.getcwd()
    name = os.path.basename(cwd)
    if not name or name in (".", "/"):
        return "global"

    # Sanitize: lowercase, replace spaces/special chars with hyphens
    sanitized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return sanitized or "global"


# ── Lifespan ──────────────────────────────────────────────

@asynccontextmanager
async def app_lifespan(app):
    namespace = _detect_namespace()
    db = MemoryDB(namespace=namespace)

    mode = "haiku-enhanced" if llm.is_available() else "storage-only"
    logger.info("Memory MCP — ns: %s, mode: %s", namespace, mode)
    yield {"db": db}


mcp = FastMCP("memory_mcp", lifespan=app_lifespan)


def _get_db(ctx: Context) -> MemoryDB:
    return ctx.request_context.lifespan_context["db"]


# ── Input Models ──────────────────────────────────────────


class IngestInput(BaseModel):
    """Smart ingest with optional LLM extraction."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(..., description="Raw text to memorize", min_length=1, max_length=50_000)
    source: str = Field(default="claude-code", max_length=100)
    to_global: bool = Field(
        default=False,
        description="If true, save to global (shared) namespace instead of project namespace",
    )
    # Manual overrides
    summary: Optional[str] = Field(default=None, max_length=1000)
    entities: Optional[list[str]] = Field(default=None)
    topics: Optional[list[str]] = Field(default=None)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    memory_type: Optional[str] = Field(default=None, max_length=50)


class SaveInput(BaseModel):
    """Direct save with all metadata."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    content: str = Field(..., min_length=1, max_length=50_000)
    summary: str = Field(default="")
    entities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = Field(default="claude-code", max_length=100)
    memory_type: str = Field(default="fact", max_length=50)
    to_global: bool = Field(default=False, description="Save to global namespace")


class QueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    question: str = Field(..., min_length=1, max_length=2000)
    scope: str = Field(default="both", description="'project', 'global', or 'both'")


class ConsolidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=2, le=100)
    scope: str = Field(default="both", description="'project', 'global', or 'both'")
    save_to: str = Field(
        default="project",
        description="Where to save insights: 'project' or 'global'",
    )


class SearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    scope: str = Field(default="both", description="'project', 'global', or 'both'")


class ListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    memory_type: Optional[str] = Field(default=None)
    scope: str = Field(default="both", description="'project', 'global', or 'both'")


class GetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: int = Field(..., ge=1)
    namespace: Optional[str] = Field(default=None, description="Explicit namespace, defaults to project")


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: int = Field(..., ge=1)
    namespace: Optional[str] = Field(default=None)


# ── Tools ─────────────────────────────────────────────────


@mcp.tool(
    name="memory_ingest",
    annotations={
        "title": "Ingest Into Memory (Smart)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def memory_ingest(params: IngestInput, ctx: Context) -> str:
    """Ingest raw text into memory. Haiku auto-extracts metadata when API key is set.
    Manual overrides always take priority. Set to_global=true for shared namespace.

    Args:
        params: text, source, to_global, optional manual overrides

    Returns:
        str: JSON with saved memory and processing mode
    """
    db = _get_db(ctx)

    extracted = llm.extract_metadata(params.text)
    mode = "haiku" if extracted else "manual"

    base = extracted or {}
    summary = params.summary or base.get("summary", params.text[:200])
    entities = params.entities if params.entities is not None else base.get("entities", [])
    topics = params.topics if params.topics is not None else base.get("topics", [])
    importance = params.importance if params.importance is not None else base.get("importance", 0.5)
    memory_type = params.memory_type or base.get("memory_type", "fact")

    memory = db.add(
        content=params.text,
        summary=summary,
        entities=entities,
        topics=topics,
        importance=importance,
        source=params.source,
        memory_type=memory_type,
        to_global=params.to_global,
    )

    return json.dumps({"status": "saved", "mode": mode, "memory": memory.to_dict()}, indent=2, default=str)


@mcp.tool(
    name="memory_save",
    annotations={
        "title": "Save Memory (Direct)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def memory_save(params: SaveInput, ctx: Context) -> str:
    """Save with all metadata provided. No LLM calls. Use for consolidation insights too.

    Args:
        params: All memory fields + to_global flag

    Returns:
        str: JSON with saved memory
    """
    db = _get_db(ctx)
    memory = db.add(
        content=params.content,
        summary=params.summary or params.content[:200],
        entities=params.entities,
        topics=params.topics,
        importance=params.importance,
        source=params.source,
        memory_type=params.memory_type,
        to_global=params.to_global,
    )
    return json.dumps({"status": "saved", "memory": memory.to_dict()}, indent=2, default=str)


@mcp.tool(
    name="memory_query",
    annotations={
        "title": "Query Memories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def memory_query(params: QueryInput, ctx: Context) -> str:
    """Ask a question across memories. scope='both' searches project + global.

    With API key: Haiku synthesizes answer.
    Without: returns raw data for Claude Code.
    """
    db = _get_db(ctx)
    memories = db.list_all(limit=100, scope=params.scope)

    if not memories:
        return json.dumps({"status": "empty", "message": "No memories stored yet."})

    mem_dicts = [m.to_dict() for m in memories]
    answer = llm.query_memories(params.question, mem_dicts)

    if answer:
        return json.dumps({"mode": "haiku", "answer": answer}, indent=2)

    return json.dumps(
        {
            "mode": "manual",
            "message": "Synthesize the answer from these memories.",
            "question": params.question,
            "memory_count": len(mem_dicts),
            "memories": mem_dicts,
        },
        indent=2, default=str,
    )


@mcp.tool(
    name="memory_consolidate",
    annotations={
        "title": "Consolidate Memories",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def memory_consolidate(params: ConsolidateInput, ctx: Context) -> str:
    """Find connections between memories and generate insights.

    With API key: Haiku analyzes and auto-saves a 'connection' memory.
    Without: returns memories for Claude Code to consolidate.
    """
    db = _get_db(ctx)
    memories = db.list_all(limit=params.limit, scope=params.scope)

    if len(memories) < 2:
        return json.dumps({"status": "skipped", "reason": "Need at least 2 memories"})

    mem_dicts = [m.to_dict() for m in memories]
    result = llm.consolidate_memories(mem_dicts)

    if result:
        insight_text = (
            f"Connections: {result.get('connections', '')}\n"
            f"Insight: {result.get('insight', '')}\n"
            f"Connected memories: {result.get('connected_ids', [])}"
        )
        saved = db.add(
            content=insight_text,
            summary=result.get("insight", ""),
            entities=[],
            topics=["consolidation"],
            importance=0.9,
            source="haiku-consolidation",
            memory_type="connection",
            to_global=(params.save_to == "global"),
        )
        return json.dumps(
            {
                "mode": "haiku",
                "status": "consolidated",
                "connections": result.get("connections", ""),
                "insight": result.get("insight", ""),
                "connected_ids": result.get("connected_ids", []),
                "saved_as_memory": saved.to_dict(),
            },
            indent=2, default=str,
        )

    return json.dumps(
        {
            "mode": "manual",
            "message": "Review memories, find connections, save with memory_save(memory_type='connection').",
            "memory_count": len(mem_dicts),
            "memories": mem_dicts,
        },
        indent=2, default=str,
    )


@mcp.tool(
    name="memory_search",
    annotations={
        "title": "Search Memories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_search(params: SearchInput, ctx: Context) -> str:
    """Keyword search across memories. scope='both' for project + global."""
    db = _get_db(ctx)
    results = db.search(params.query, params.limit, scope=params.scope)
    return json.dumps(
        {"count": len(results), "results": [m.to_dict() for m in results]},
        indent=2, default=str,
    )


@mcp.tool(
    name="memory_list",
    annotations={
        "title": "List Memories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_list(params: ListInput, ctx: Context) -> str:
    """Browse memories with pagination, type filter, and scope."""
    db = _get_db(ctx)
    memories = db.list_all(
        limit=params.limit, offset=params.offset,
        memory_type=params.memory_type, scope=params.scope,
    )
    stats = db.stats(scope=params.scope)
    return json.dumps(
        {
            "count": len(memories),
            "total": stats.get("total", 0),
            "offset": params.offset,
            "namespace": db.namespace,
            "memories": [m.to_dict() for m in memories],
        },
        indent=2, default=str,
    )


@mcp.tool(
    name="memory_get",
    annotations={
        "title": "Get Memory",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_get(params: GetInput, ctx: Context) -> str:
    """Get a memory by ID. Specify namespace if not in current project."""
    db = _get_db(ctx)
    memory = db.get(params.memory_id, namespace=params.namespace)
    if memory:
        return json.dumps({"memory": memory.to_dict()}, indent=2, default=str)
    return json.dumps({"status": "not_found", "memory_id": params.memory_id})


@mcp.tool(
    name="memory_delete",
    annotations={
        "title": "Delete Memory",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_delete(params: DeleteInput, ctx: Context) -> str:
    """Delete a memory. Specify namespace if different from current project."""
    db = _get_db(ctx)
    deleted = db.delete(params.memory_id, namespace=params.namespace)
    return json.dumps({"status": "deleted" if deleted else "not_found", "memory_id": params.memory_id})


@mcp.tool(
    name="memory_status",
    annotations={
        "title": "Memory Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_status(ctx: Context) -> str:
    """Stats: counts per namespace, type breakdown, current mode."""
    db = _get_db(ctx)
    stats = db.stats()
    stats["mode"] = "haiku-enhanced" if llm.is_available() else "storage-only"
    stats["model"] = llm.MODEL if llm.is_available() else None
    return json.dumps(stats, indent=2)


@mcp.tool(
    name="memory_clear",
    annotations={
        "title": "Clear Memories",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_clear(ctx: Context) -> str:
    """Clear all memories in current project namespace. Does NOT touch global."""
    db = _get_db(ctx)
    count = db.clear()
    return json.dumps({"status": "cleared", "namespace": db.namespace, "deleted_count": count})


# ── Entry point ───────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
