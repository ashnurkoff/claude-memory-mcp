# Memory MCP — Usage Guide for Claude Code

You have persistent memory via MCP tools backed by Upstash Redis, shared across all your Claude Code sessions.

## Namespaces

- **Project namespace** (`MEMORY_NAMESPACE`) — isolated per-project. Only this agent sees it.
- **Global namespace** — shared across ALL projects and agents.
- **scope="both"** (default) — queries and listings merge project + global.

To save to global: set `to_global: true` on `memory_ingest` or `memory_save`.

## Haiku-Enhanced Mode (ANTHROPIC_API_KEY set)

```
memory_ingest({ text: "We decided to use PostgreSQL", source: "meeting" })
→ Haiku auto-extracts summary, entities, topics, importance, type

memory_query({ question: "What database decisions?" })
→ Haiku synthesizes answer with [Memory #N] citations

memory_consolidate({})
→ Haiku finds connections, auto-saves insight
```

## Storage-Only Mode (no API key)

Extract metadata yourself:

```
memory_save({
  content: "We decided to use PostgreSQL",
  summary: "PostgreSQL chosen", entities: ["PostgreSQL"],
  topics: ["database"], importance: 0.8, memory_type: "decision"
})
```

For queries: `memory_search` + `memory_list`, then synthesize yourself.

## Memory Types

- `fact` — project facts, config
- `decision` — choices with rationale
- `insight` — observations, learnings
- `todo` — action items
- `connection` — consolidation insights

## Cross-Agent Workflow

When working on multiple projects simultaneously:
- Save project-specific decisions to project namespace (default)
- Save cross-cutting insights to global: `to_global: true`
- Query with `scope: "both"` to see everything, or `scope: "global"` for shared only
