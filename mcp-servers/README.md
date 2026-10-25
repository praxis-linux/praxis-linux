# MCP Servers

First-party Model Context Protocol servers for Praxis.

## Status

The first server, [`praxis-fs`](praxis-fs/README.md), was integrated end-to-end at milestone **M0.8** — see [Roadmap](../docs/ROADMAP.md). It is read-only.

## Servers

| Server | Purpose | Status |
|---|---|---|
| [`praxis-fs`](praxis-fs/README.md) | Filesystem — list, search, read | Built (M0.8). Write and move wait for consent and undo (M0.12). |
| `praxis-shell` | Sandboxed shell execution | Planned (M0.9) |
| `praxis-browser` | Headless browser / web search | Planned (M0.9) |
| `praxis-code` | Code execution (Python, Node, Bash) | Planned (M0.11) |
| `praxis-settings` | System settings — audio, display, network | Planned (M1.x) |

## Structure

```
mcp-servers/
└── praxis-fs/
    ├── pyproject.toml
    ├── src/praxis_fs/
    │   ├── fs.py         # The rules: what may be read, and how
    │   └── server.py     # Those rules as MCP tools over stdio
    └── tests/
```

All servers implement the [Model Context Protocol](https://modelcontextprotocol.io) and are written in Python. Each is its own project with its own lockfile; the agent depends on the ones it starts.
