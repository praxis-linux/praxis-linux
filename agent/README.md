# Agent

Praxis agent runtime — router, memory, MCP client. Python + FastAPI + PydanticAI.

## Status

Placeholder. Stood up at milestone **M0.6** — see [Roadmap](../docs/ROADMAP.md).

## Responsibilities

- **Router** — classifies intent and decides local vs cloud per turn
- **Memory** — short-term (conversation), mid-term (project), long-term (vector)
- **MCP client** — connects to filesystem, shell, browser, code, settings servers
- **Tool dispatch** — typed tool calls via PydanticAI
- **A2UI emission** — turns tool results into structured UI payloads for the Canvas

## Planned structure (M0.6)

```
agent/
├── pyproject.toml
├── litellm_config.yaml       # Already present — see below
├── src/praxis_agent/
│   ├── __init__.py
│   ├── server.py             # FastAPI app
│   ├── router.py             # Local-vs-cloud + mode classifier
│   ├── memory/               # Qdrant + SQLite + DuckDB
│   ├── mcp/                  # MCP client wrappers
│   ├── tools/                # Typed tools (PydanticAI)
│   ├── ui/                   # A2UI payload builders
│   └── audit.py              # Cloud-call audit log
└── tests/
```

## Setup (placeholder — runnable at M0.6)

```bash
cd agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
litellm --config litellm_config.yaml --port 4000 &
uvicorn praxis_agent.server:app --reload --port 8000
```

## LiteLLM proxy

`litellm_config.yaml` defines the model abstraction. The agent never talks to Ollama or Anthropic directly — it talks to LiteLLM, which routes by model name. This is what makes "bring your own model" trivial.
