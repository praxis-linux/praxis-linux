# Canvas

The Praxis desktop shell. Tauri 2 + React 19 + TypeScript.

## Status

Placeholder. Scaffolded at milestone **M0.5** — see [Roadmap](../docs/ROADMAP.md).

## Architecture

The Canvas is a single primary surface with three layers:

```
┌──────────────────────────────────┬─────────────────────┐
│                                  │                     │
│  Workspace Layer (morphing)      │  Context Rail       │
│                                  │  (optional)         │
│  One of N modes assembled by     │                     │
│  the agent from the catalog:     │  · open files       │
│  Conversation / File / Code /    │  · recent commands  │
│  Document / Settings / …         │  · memory in use    │
│                                  │  · tools in-flight  │
├──────────────────────────────────┴─────────────────────┤
│  Intent Layer (always visible)                          │
│  text · voice · drag-drop · screenshot region           │
└─────────────────────────────────────────────────────────┘
```

**Intent layer** — always present. Multimodal input: typed text, voice (whisper.cpp), drag-and-drop files, screenshot region selection.

**Workspace layer** — morphs per intent. The agent classifies intent and assembles a layout from the component catalog. Available modes:

| Mode | Trigger examples |
|---|---|
| Conversation | general Q&A, brainstorming |
| File | "open downloads", "find PDFs about X", file operations |
| Code | "edit server.py", "run tests", "show diff" |
| Document | "draft a proposal", "summarise this PDF" |
| Spreadsheet | "show me the CSV as a table", data editing |
| Browser | "look up X", web research |
| Settings | "change display resolution", system config |
| Terminal | "run `docker ps`", shell tasks |
| Media | image/video viewing and editing |
| Diagram | architecture diagrams, flowcharts |
| Dashboard | multi-widget status views |

**Context rail** — optional right panel. Shows what the agent "knows right now": open files, recent commands, retrieved memory, tool calls in flight. Critical for trust — without this, users can't see what the agent is doing.

## Planned structure (M0.5)

```
canvas/
├── package.json
├── pnpm-lock.yaml
├── src/
│   ├── main.tsx              # React entry
│   ├── canvas/
│   │   ├── Canvas.tsx        # Top-level surface, layer compositor
│   │   ├── layers/
│   │   │   ├── IntentLayer.tsx     # Input bar, voice, drag-drop
│   │   │   ├── WorkspaceLayer.tsx  # Mode renderer
│   │   │   └── ContextRail.tsx     # Agent state panel
│   │   └── modes/            # Conversation / File / Code / Doc / ...
│   ├── a2ui/                 # A2UI renderer + component catalog
│   └── lib/                  # Shared utilities
├── src-tauri/
│   ├── Cargo.toml
│   ├── Cargo.lock            # committed — binary crate needs reproducible builds
│   ├── tauri.conf.json
│   └── src/
│       ├── main.rs           # Tauri entry
│       ├── ipc.rs            # Bridge to agent
│       └── system/           # File access, shell, etc. (sandboxed)
└── public/
```

## Planned dependencies

- React 19, TypeScript 5
- shadcn/ui + Tailwind v4
- @ai-sdk/react (Vercel AI SDK — streaming)
- A2UI React renderer (when published) — until then, internal renderer
- CodeMirror 6 (Code mode)
- TipTap (Doc mode)
- Tanstack Query, Zustand, Zod

## Setup (placeholder — runnable at M0.5)

```bash
cd canvas
pnpm install
pnpm tauri dev
```
