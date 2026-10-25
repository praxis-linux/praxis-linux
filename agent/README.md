# Agent

Praxis agent runtime. Python + FastAPI + PydanticAI.

## Status

Stood up at milestone **M0.6**; reads the user's files through the `praxis-fs` MCP server since **M0.8**; streams its replies and remembers conversations since **M0.9**. Per-turn routing and long-term memory are not built yet — see the [Roadmap](../docs/ROADMAP.md).

## What it does today

1. Receives an intent (`POST /intent`) or a UI action (`POST /action`) from the Canvas, and the conversation so far from its store.
2. Picks a model alias and writes an audit row. If the row cannot be written, no model is called.
3. Asks the model, through the LiteLLM proxy, for one **step** at a time. A step is either a file lookup or the reply: a mode, a title and a list of blocks (`text`, `code`, `cards`, `form`).
4. Runs each lookup against `praxis-fs`, records it in the audit log, tells the Canvas what it is doing ("Searching for *.py…"), and gives the model the result. After three lookups the model has to reply.
5. Draws the reply while the model writes it. What was looked up goes on the screen itself — the listing or the file's text straight from the tool, not the model's retelling. Everything reaches the Canvas as A2UI v0.9 messages, streamed as NDJSON.
6. Stores the turn, and redraws the conversation trail in the Canvas's context rail.

Some clicks never reach a model: a file or folder (`open`) goes straight to `praxis-fs` and back, and a turn in the trail (`revisit`) brings back that turn's stored screen. `POST /resume` does the same for the last turn when the Canvas opens.

The contract with the Canvas is specified in [ADR-003](../docs/architecture/ADR-003-ui-wire-format.md).

## Structure

```
agent/
├── pyproject.toml
├── litellm_config.yaml       # Model aliases — see below
├── src/praxis_agent/
│   ├── server.py             # FastAPI app: /intent, /action, /resume, /health
│   ├── router.py             # Which alias serves a turn; local or cloud
│   ├── planner.py            # The step loop: lookups, then the reply, streamed
│   ├── memory.py             # Conversations: stored turns and what the model is shown
│   ├── tools/
│   │   └── files.py          # praxis-fs over MCP: the lookups and their results
│   ├── ui/
│   │   ├── plan.py           # What the model fills in, and the screen built from it
│   │   └── a2ui.py           # Screen → A2UI v0.9 messages
│   └── audit.py              # Audit log of every model call and tool call
└── tests/
    └── golden/               # Builder output, also read by the Canvas tests
```

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
uv run uvicorn praxis_agent.server:create_app --factory --host 127.0.0.1 --port 7729
```

The proxy is not installed into the agent's environment: it is a separate process, and its dependency pins would otherwise hold back the agent's. Its version is pinned here; raise it deliberately.

Try it without the Canvas:

```bash
curl -N -X POST http://127.0.0.1:7729/intent \
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
| `PRAXIS_CONVERSATIONS_DB` | `~/.praxis/conversations.db` | Conversation store location |
| `PRAXIS_FS_ROOTS` | the home folder | Folders the agent may read — see [`praxis-fs`](../mcp-servers/praxis-fs/README.md) |
| `PRAXIS_FS_COMMAND` | `python -m praxis_fs` | Command that starts the filesystem server |

## Who can talk to the agent

The agent listens on 127.0.0.1 only. It answers requests that name it as `127.0.0.1` or `localhost` and, if they carry an `Origin`, come from the Canvas. That stops a web page that points its own domain at this machine (DNS rebinding) from using the agent to read your files. It does not stop another program running on this machine as any user: there is no secret between the Canvas and the agent yet. That needs the desktop shell to start the agent and hand the Canvas a token.

## Files and the model

What the model looks up is sent to the model that serves the turn. With a local alias nothing leaves the machine. With a cloud alias, the names and contents of the files it looks at go to that provider; the Canvas marks the turn as a cloud call and the audit log records it, but there is no per-file consent prompt yet (M0.12).

`praxis-fs` refuses hidden files and anything outside the allowed folders. When it refuses, the Canvas says so in the agent's own words, because a small model's account of a refusal is often that the file does not exist.

## Conversations

Each finished turn is stored in the conversation store: the request, the screen that was drawn, and every message the model was sent and answered — **including the text of any file it read**. The store is on this machine, in `~/.praxis`, which `praxis-fs` will not read. "New" in the Canvas starts another conversation and keeps the old; "Delete…" removes the current one (`POST /forget`) and compacts the database so its text is gone from the file. The audit log keeps its records, which hold no prompt text or file contents. Nothing expires old conversations on its own yet.

## The kill switch

`POST /stop` ends every turn the agent is working on, in every conversation: each model call is cut off and recorded as cancelled. The Canvas sends it when you press Esc or its Stop button while the agent works.

The model is shown the last ten turns of a conversation: the latest in full, so it can refer to what it just found, and the earlier ones as request and reply only. When the screen shows a file or folder, the request goes to the model with a note saying which, so "summarise this" works.

## LiteLLM proxy

`litellm_config.yaml` defines the model aliases. The agent never talks to Ollama or a cloud provider directly — it talks to LiteLLM, which routes by alias. This is what makes "bring your own model" trivial.

An alias counts as local only if its name starts with `local-`. Anything else is reported to the Canvas and audited as a cloud call.

## Audit log

Two tables in the audit database, each row written before the call it records:

- `model_calls`: when, which session, which alias, local or cloud, what triggered it, how long the request was (as sent, with the note about what is on screen; earlier turns and lookup results come on top), how many model requests it took, how it ended and how long it took. Prompt text is not in the audit log; it is in the conversation store.
- `tool_calls`: when, which session, which tool, with what arguments, whether the model or the user asked for it, and how it ended.

```bash
sqlite3 ~/.praxis/audit.db 'SELECT started_at, tool, arguments, asked_by, status FROM tool_calls'
```

## Tests

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests never call a real model; they do start a real `praxis-fs`. After changing `ui/a2ui.py`, regenerate the golden files with `UPDATE_GOLDEN=1 uv run pytest` and run the Canvas tests, which read it.
