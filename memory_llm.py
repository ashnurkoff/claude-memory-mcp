"""
Optional LLM-powered memory processing.
Activates ONLY when ANTHROPIC_API_KEY is present in environment.
Uses Haiku for cheap, fast background processing.
"""

import json
import os
import logging
from typing import Optional

logger = logging.getLogger("memory-llm")

MODEL = os.environ.get("MEMORY_MODEL", "claude-haiku-4-5-20251001")
_client = None


def is_available() -> bool:
    """Check if LLM processing is available (API key present)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def _call_llm(system: str, user_message: str, max_tokens: int = 1024) -> Optional[str]:
    try:
        client = _get_client()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None


def _parse_json(raw: str) -> Optional[dict]:
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return None


# ── Extract ───────────────────────────────────────────────

EXTRACT_SYSTEM = """You are a memory extraction engine. Given raw text, extract structured information.

Respond with ONLY a valid JSON object (no markdown fences):
- "summary": Concise 1-2 sentence summary
- "entities": List of named entities (people, companies, technologies, projects)
- "topics": List of topic categories
- "importance": Float 0.0-1.0
- "memory_type": One of "fact", "decision", "insight", "todo"

Example:
{"summary": "Team decided to use PostgreSQL for JSONB support", "entities": ["PostgreSQL"], "topics": ["database", "architecture"], "importance": 0.8, "memory_type": "decision"}"""


def extract_metadata(text: str) -> Optional[dict]:
    if not is_available():
        return None
    raw = _call_llm(EXTRACT_SYSTEM, text)
    return _parse_json(raw) if raw else None


# ── Consolidate ──────────────────────────────────────────

CONSOLIDATE_SYSTEM = """You are a memory consolidation engine. Like the brain during sleep, find connections between memories.

Respond with ONLY a valid JSON object:
- "connections": How these memories relate
- "insight": Higher-level pattern or insight
- "connected_ids": List of most strongly connected memory IDs

Be specific and actionable."""


def consolidate_memories(memories: list[dict]) -> Optional[dict]:
    if not is_available() or len(memories) < 2:
        return None

    parts = []
    for m in memories:
        parts.append(
            f"[Memory #{m['id']}] (ns:{m.get('namespace','?')}, importance:{m.get('importance',0.5)})\n"
            f"  Summary: {m.get('summary','')}\n"
            f"  Entities: {', '.join(m.get('entities',[]))}\n"
            f"  Topics: {', '.join(m.get('topics',[]))}"
        )

    raw = _call_llm(CONSOLIDATE_SYSTEM, f"Consolidate:\n\n" + "\n\n".join(parts), max_tokens=2048)
    return _parse_json(raw) if raw else None


# ── Query ─────────────────────────────────────────────────

QUERY_SYSTEM = """You are a memory retrieval engine. Answer the question using stored memories.
Reference memories as [Memory #N]. Be concise. Prioritize high-importance and recent items.
Note which namespace each memory comes from (global vs project)."""


def query_memories(question: str, memories: list[dict]) -> Optional[str]:
    if not is_available() or not memories:
        return None

    parts = []
    for m in memories:
        parts.append(
            f"[Memory #{m['id']}] (ns:{m.get('namespace','?')}, importance:{m.get('importance',0.5)}, "
            f"type:{m.get('memory_type','fact')}, created:{m.get('created_at','')})\n"
            f"  {m.get('summary','')}\n"
            f"  Content: {m.get('content','')[:500]}"
        )

    return _call_llm(
        QUERY_SYSTEM,
        f"## Memories\n\n" + "\n\n".join(parts) + f"\n\n---\nQuestion: {question}",
        max_tokens=2048,
    )
