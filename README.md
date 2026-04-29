# Praxis Linux

> The Linux distribution where the desktop is the agent.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status: Phase 0 — Pre-Alpha](https://img.shields.io/badge/Status-Phase_0_Pre--Alpha-orange.svg)](docs/ROADMAP.md)

Praxis rejects the idea that AI should be a sidebar. The Canvas is one surface that becomes your file explorer, your editor, your document, your settings panel — generated from intent, composed from a vetted catalog of components.

The name is from the Greek *praxis* (πρᾶξις) — *action; the moment thought becomes deed*.

**👉 Start here: [The Manifesto](MANIFESTO.md)**

---

## Status

**Phase 0 · Spike.** We are building the Canvas as a Linux app first. Once the core idea is proven, Phase 1 wraps it into a bootable ISO. See the [Roadmap](docs/ROADMAP.md) for the full plan.

This repository is intentionally early. Star the repo to follow along.

## What's in here

| Directory      | Purpose |
|----------------|---------|
| `canvas/`      | Tauri 2 + React desktop application — the Canvas |
| `agent/`       | Python agent runtime (router, memory, MCP client) |
| `mcp-servers/` | First-party Model Context Protocol servers |
| `components/`  | A2UI component catalog the agent renders |
| `docs/`        | Architecture decisions and the roadmap |
| `examples/`    | End-to-end demo flows |

## Quickstart (Phase 0 dev preview)

> Not runnable yet. Setup instructions land with milestone **M0.5**. Watch this space.

## Architecture in one diagram

```
┌─────────────────────────────────────────────────┐
│  Canvas (Tauri + React + A2UI renderer)         │
├─────────────────────────────────────────────────┤
│  Agent Runtime (router · memory · MCP client)   │
├─────────────────────────────────────────────────┤
│  Model Plane     │   Capability Plane           │
│  Ollama │ vLLM   │   MCP servers (fs, shell,    │
│  LiteLLM (cloud) │   browser, code, settings)   │
├─────────────────────────────────────────────────┤
│  Memory: Qdrant · SQLite · DuckDB               │
├─────────────────────────────────────────────────┤
│  Linux base (Arch upstream · Hyprland · Btrfs)  │
└─────────────────────────────────────────────────┘
```

Detail: [`docs/architecture/ADR-001-strategic-positioning.md`](docs/architecture/ADR-001-strategic-positioning.md), [`docs/architecture/ADR-002-name-and-namespace.md`](docs/architecture/ADR-002-name-and-namespace.md)

## Naming convention

The brand operates on two registers: **Praxis** in casual flow (a single confident word) and **Praxis Linux** in formal and SEO contexts. This mirrors Arch Linux, Kali Linux, and Linux Mint — formal name in headers, casual name in conversation. GitHub org and package namespace: `praxis-linux`.

## Contributing

Praxis is open from day one but moving deliberately. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a PR. Issues and discussions are open; we'd rather hear "have you considered X" than receive a 2000-line PR right now.

## License

Apache 2.0 — see [`LICENSE`](LICENSE). We chose Apache (not GPL) deliberately, to maximise downstream adoption by both individuals and companies.
