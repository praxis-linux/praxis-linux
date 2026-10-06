# Agent

Praxis agent runtime. Python + FastAPI + PydanticAI.

## Status

Stood up at milestone **M0.6**; reads the user's files through the `praxis-fs` MCP server since **M0.8**. Memory and per-turn routing are not built yet — see the [Roadmap](../docs/ROADMAP.md).

## What it does today

1. Receives an intent (`POST /intent`) or a UI action (`POST /action`) from the Canvas.
2. Picks a model alias and writes an audit row. If the row cannot be written, no model is called.
3. Asks the model, through the LiteLLM proxy, for one **step** at a time. A step is either a file lookup or the reply: a mode, a title and a list of blocks (`text`, `code`, `cards`, `form`).
4. Runs each lookup against `praxis-fs`, records it in the audit log, and gives the model the result. After three lookups the model has to reply.
5. Puts what was looked up on the screen itself — the listing or the file's text straight from the tool, not the model's retelling — and compiles the screen into A2UI v0.9 messages, streamed back as NDJSON.

Clicking a file or folder in the Canvas sends an `open` action. That goes straight to `praxis-fs` and back; no model is called.

The contract with the Canvas is specified in [ADR-003](../docs/architecture/ADR-003-ui-wire-format.md).

## Structure

```
agent/
├── pyproject.toml
├── litellm_config.yaml       # Model aliases — see below
├── src/praxis_agent/
│   ├── server.py             # FastAPI app: /intent, /action, /health
│   ├── router.py             # Which alias serves a turn; local or cloud
│   ├── planner.py            # The step loop: lookups, then the reply
│   ├── tools/
│   │   └── files.py          # praxis-fs over MCP: the lookups and their results
│   ├── ui/
│   │   ├── plan.py           # What the model fills in, and the screen built from it
│   │   └── a2ui.py           # Screen → A2UI v0.9 messages
│   └── audit.py              # Audit log of every model call and tool call
└── tests/
    └── golden/               # Builder output, also read by the Canvas tests
```

Still to come: `memory/` (M0.9).

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and a running [Ollama](https://ollama.com).

```bash
cd agent
ollama pull qwen2.5:7b
uv sync

# Terminal 1 — the LiteLLM proxy, in its own environment
uv tool run --from 'litellm[proxy]==1.103.1' litellm \
  --config litellm_config.yaml --host 127.0.0.1 --port 4000

# Terminal 2 — the agent (it starts praxis-fs itself)
uv run uvicorn praxis_agent.server:create_app --factory --host 127.0.0.1 --port 8000
```

The proxy is not installed into the agent's environment: it is a separate process, and its dependency pins would otherwise hold back the agent's. Its version is pinned here; raise it deliberately.

Try it without the Canvas:

```bash
curl -N -X POST http://127.0.0.1:8000/intent \
  -H 'Content-Type: application/json' \
  -H 'X-Praxis-Session: my-session-1' \
  -d '{"intent": "what is in my Documents folder?"}'
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PRAXIS_MODEL` | `local-fast` | Model alias that serves every turn |
| `PRAXIS_LITELLM_URL` | `http://127.0.0.1:4000` | Where the LiteLLM proxy listens |
| `LITELLM_MASTER_KEY` | unset | Sent to the proxy as the API key, if the proxy requires one |
| `PRAXIS_AUDIT_DB` | `~/.praxis/audit.db` | Audit log location |
| `PRAXIS_FS_ROOTS` | the home folder | Folders the agent may read — see [`praxis-fs`](../mcp-servers/praxis-fs/README.md) |
| `PRAXIS_FS_COMMAND` | `python -m praxis_fs` | Command that starts the filesystem server |

## Files and the model

What the model looks up is sent to the model that serves the turn. With a local alias nothing leaves the machine. With a cloud alias, the names and contents of the files it looks at go to that provider; the Canvas marks the turn as a cloud call and the audit log records it, but there is no per-file consent prompt yet (M0.12).

`praxis-fs` refuses hidden files and anything outside the allowed folders. When it refuses, the Canvas says so in the agent's own words, because a small model's account of a refusal is often that the file does not exist.

## LiteLLM proxy

`litellm_config.yaml` defines the model aliases. The agent never talks to Ollama or a cloud provider directly — it talks to LiteLLM, which routes by alias. This is what makes "bring your own model" trivial.

An alias counts as local only if its name starts with `local-`. Anything else is reported to the Canvas and audited as a cloud call.

## Audit log

Two tables in the audit database, each row written before the call it records:

- `model_calls`: when, which session, which alias, local or cloud, what triggered it, how many characters were sent, how many requests it took, how it ended and how long it took. Prompt text is not stored.
- `tool_calls`: when, which session, which tool, with what arguments, whether the model or the user asked for it, and how it ended.

```bash
sqlite3 ~/.praxis/audit.db 'SELECT started_at, tool, arguments, asked_by, status FROM tool_calls'
```

## Tests

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests never call a real model; they do start a real `praxis-fs`. After changing `ui/a2ui.py`, regenerate the golden file with `UPDATE_GOLDEN=1 uv run pytest` and run the Canvas tests, which read it.
