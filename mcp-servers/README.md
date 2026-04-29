# MCP Servers

First-party Model Context Protocol servers for Praxis.

## Status

Placeholder. First server (filesystem) integrated at milestone **M0.8** — see [Roadmap](../docs/ROADMAP.md).

## Planned servers

| Server | Purpose | Milestone |
|---|---|---|
| `praxis-fs` | Filesystem — read, write, move, search | M0.8 |
| `praxis-shell` | Sandboxed shell execution | M0.9 |
| `praxis-browser` | Headless browser / web search | M0.9 |
| `praxis-code` | Code execution (Python, Node, Bash) | M0.11 |
| `praxis-settings` | System settings — audio, display, network | M1.x |

## Planned structure (M0.8)

```
mcp-servers/
├── praxis-fs/
│   ├── pyproject.toml
│   └── src/praxis_fs/
│       └── server.py
├── praxis-shell/
└── ...
```

All servers implement the [Model Context Protocol](https://modelcontextprotocol.io) and are written in Python.
