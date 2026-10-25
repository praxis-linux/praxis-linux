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
| `agent/`       | Python agent runtime (router, planner, file lookups over MCP, conversation store, audit log) |
| `mcp-servers/` | First-party Model Context Protocol servers: `praxis-fs` (files) and `praxis-run` (running a file in a sandbox) |
| `components/`  | Planned Praxis components for the A2UI catalog |
| `docs/`        | Architecture decisions and the roadmap |
| `examples/`    | End-to-end demo flows |

## Quickstart (Phase 0 dev preview)

You need [Ollama](https://ollama.com), [uv](https://docs.astral.sh/uv/), Node 20+ (pnpm comes through `corepack`), and for the desktop window Rust with the [Tauri prerequisites](https://tauri.app/start/prerequisites/).

```bash
ollama pull qwen2.5:7b
cd agent && uv sync && cd ..

python scripts/dev.py          # model proxy, agent and the Canvas desktop window
python scripts/dev.py --web    # or the Canvas in a browser, at http://localhost:1420
python scripts/dev.py --wsl    # on Windows: everything in WSL, the window on your desktop
```

Ctrl+C stops everything.

**On Windows**, the desktop window is easiest through WSL: `--wsl` copies your working tree into WSL (Ubuntu-24.04 unless you name another distro) and starts everything there. Ollama can stay on Windows; your API keys are passed across. Inside WSL you need Rust, uv, Node with corepack and the Tauri prerequisites for Linux. The agent then reads your WSL home folder, not your Windows one. Building natively on Windows also works, but not with Smart App Control on: it blocks the helper programs a Rust build compiles and runs. To run the pieces yourself, see [`agent/README.md`](agent/README.md) and [`canvas/README.md`](canvas/README.md).

Type an intent in the bar at the bottom — try "what is in my Documents folder?". The agent answers from a local model. It can look at the files in your home folder, hidden ones excepted. Open a file and press **Edit** to change it, or **Run** to run it (Linux, with bubblewrap installed). Ask the agent to change a file and it shows the change as a diff: nothing is written until you press **Apply**, and every change can be undone.

To use a cloud model instead, set `ANTHROPIC_API_KEY` and add `--model cloud-fast` (Claude Haiku) or `--model cloud-strong` (Claude Opus). The file names and contents the agent looks at then go to Anthropic, and the Canvas says so before each request.

### The AppImage

Praxis also comes as one file for Linux, with the agent and the model proxy inside, on a Python of their own. The [AppImage workflow](.github/workflows/appimage.yml) builds it for every change to `main`; to build it yourself, run `scripts/build-appimage.sh`.

```bash
ollama pull qwen2.5:7b               # the local model; Ollama itself is not inside
sudo apt install bubblewrap          # for Run (sudo pacman -S bubblewrap on Arch)
chmod +x Praxis*.AppImage
./Praxis*.AppImage
```

It needs a desktop Linux: it brings its own GTK and WebKit, and uses the system's graphics drivers, X11 or Wayland libraries and fonts, which every desktop install has. Tested on Ubuntu 24.04 and Arch Linux. Run uses the system's `python3`, `bash` and `node`.

Settings are environment variables, as for the agent: `PRAXIS_MODEL=cloud-fast` with `ANTHROPIC_API_KEY` for a cloud model, `PRAXIS_FS_ROOTS` for the folders it may use. The window starts the agent and stops it when it closes; the agent's and the proxy's logs are in `~/.local/share/com.praxislinux.canvas/logs`. Without FUSE (in containers, or WSL), run it with `--appimage-extract-and-run`.

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

Detail: [`ADR-001` — strategic positioning](docs/architecture/ADR-001-strategic-positioning.md), [`ADR-002` — name and namespace](docs/architecture/ADR-002-name-and-namespace.md), [`ADR-003` — UI wire format](docs/architecture/ADR-003-ui-wire-format.md)

## Naming convention

The brand operates on two registers: **Praxis** in casual flow (a single confident word) and **Praxis Linux** in formal and SEO contexts. This mirrors Arch Linux, Kali Linux, and Linux Mint — formal name in headers, casual name in conversation. GitHub org and package namespace: `praxis-linux`.

## Contributing

Praxis is open from day one but moving deliberately. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a PR. Issues and discussions are open; we'd rather hear "have you considered X" than receive a 2000-line PR right now.

## License

Apache 2.0 — see [`LICENSE`](LICENSE). We chose Apache (not GPL) deliberately, to maximise downstream adoption by both individuals and companies.
