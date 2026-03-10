# Claude Memory MCP Server

Persistent, cross-session memory for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) powered by Upstash Redis and the Model Context Protocol (MCP).

Give your Claude Code agents the ability to remember decisions, insights, and context across sessions and projects — with automatic namespace isolation and optional AI-powered metadata extraction.

## Features

- **Persistent memory** — survives across sessions, powered by Upstash Redis (REST API)
- **Project isolation** — each project gets its own namespace automatically (based on working directory)
- **Shared global memory** — cross-project facts (preferences, timezone, tech stack) accessible everywhere
- **Haiku-enhanced mode** — optional metadata extraction via Claude Haiku (summary, entities, topics, importance)
- **10 MCP tools** — full memory lifecycle: ingest, query, search, consolidate, and more
- **Zero config per project** — namespace auto-detected from CWD, no `.mcp.json` needed

## Architecture

```
Claude Code (project: launcher)  ──┐
Claude Code (project: api)       ──┤──→ Upstash Redis (REST)
Claude Code (project: frontend)  ──┘          │
                                              ├── mem:launcher:*   (isolated)
                                              ├── mem:api:*        (isolated)
                                              ├── mem:frontend:*   (isolated)
                                              └── mem:global:*     (shared)
```

Each agent reads/writes its own project namespace + shared global namespace. Scope parameter controls visibility: `"project"`, `"global"`, or `"both"` (default).

## Quick Start

### 1. Create an Upstash Redis database

Sign up at [console.upstash.com](https://console.upstash.com) and create a Redis database. Copy the **REST URL** and **REST Token** from the database details page.

### 2. Install dependencies

```bash
cd claude-memory-mcp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Register with Claude Code

```bash
claude mcp add --scope user memory \
  -e UPSTASH_REDIS_REST_URL="https://your-db.upstash.io" \
  -e UPSTASH_REDIS_REST_TOKEN="your-token" \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -- /path/to/claude-memory-mcp/.venv/bin/python /path/to/claude-memory-mcp/server.py
```

> `ANTHROPIC_API_KEY` is optional — enables Haiku-enhanced mode for automatic metadata extraction.

### 4. Verify

In any Claude Code session:

```
Show memory_status
```

## Namespace Resolution

The server determines the project namespace using the following priority:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | `MEMORY_NAMESPACE` env var | `MEMORY_NAMESPACE=my-app` → `my-app` |
| 2 | Working directory name | CWD = `~/projects/launcher` → `launcher` |
| 3 | Fallback | `global` |

To save to the shared global namespace, use `to_global: true` on `memory_ingest` or `memory_save`.

## Tools

| Tool | Description | Haiku |
|------|-------------|:-----:|
| `memory_ingest` | Smart ingest — Haiku auto-extracts metadata (summary, entities, topics, importance, type) | Yes |
| `memory_save` | Direct save with all metadata provided manually | — |
| `memory_query` | Ask questions across memories — Haiku synthesizes answers with citations | Yes |
| `memory_consolidate` | Find connections between memories, generate insights | Yes |
| `memory_search` | Keyword search across content, summary, entities, topics | — |
| `memory_list` | Browse memories with pagination, type filter, and scope | — |
| `memory_get` | Retrieve a single memory by ID | — |
| `memory_delete` | Delete a memory by ID | — |
| `memory_status` | Show stats: counts, types, mode, model | — |
| `memory_clear` | Clear all memories in current project namespace (does not touch global) | — |

## Modes

| Mode | Condition | Behavior |
|------|-----------|----------|
| **Haiku-enhanced** | `ANTHROPIC_API_KEY` is set | Auto-extracts metadata on ingest, synthesizes answers on query, finds connections on consolidate |
| **Storage-only** | No API key | You provide metadata manually; query/consolidate return raw data for Claude Code to process |

## Environment Variables

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `UPSTASH_REDIS_REST_URL` | Yes | — | Upstash REST endpoint |
| `UPSTASH_REDIS_REST_TOKEN` | Yes | — | Upstash REST token |
| `MEMORY_NAMESPACE` | — | auto-detect | Override project namespace |
| `ANTHROPIC_API_KEY` | — | — | Enables Haiku-enhanced mode |
| `MEMORY_MODEL` | — | `claude-haiku-4-5-20251001` | LLM model for metadata extraction |

## Usage Examples

**Save a project decision:**

```text
Remember: we use Tailwind + shadcn/ui for styling
→ memory_ingest → saved to project namespace
```

**Save a global fact:**

```text
Save to global memory: my timezone is UTC+4
→ memory_ingest with to_global: true → saved to global namespace
```

**Query across all memories:**

```text
What architecture decisions have we made?
→ memory_query → Haiku synthesizes answer from project + global
```

**Auto-save after tasks:**
With the included `CLAUDE.md` instructions, Claude Code automatically saves memories after completing significant tasks (bug fixes, architecture decisions, config changes).

## Project Structure

```
claude-memory-mcp/
├── server.py           # MCP server — 10 tools, lifespan, namespace detection
├── memory_db.py        # Upstash Redis storage layer with namespace support
├── memory_llm.py       # Optional Haiku engine for metadata extraction
├── CLAUDE.md           # Instructions for Claude Code agents
├── requirements.txt    # Python dependencies
└── README.md
```

## CLAUDE.md

Copy the included `CLAUDE.md` to your project root or `~/.claude/CLAUDE.md` (global). It instructs Claude Code to:

- Automatically save memories after completing tasks
- Use `memory_ingest` for explicit "remember" requests
- Query memory when asked about past decisions
- Check `memory_list` at session start for recent context

## License

MIT
