# Canvas

The Praxis desktop shell. Tauri 2 + React 19 + TypeScript.

## Status

Placeholder. Scaffolded at milestone **M0.5** — see [Roadmap](../docs/ROADMAP.md).

## Planned structure (M0.5)

```
canvas/
├── package.json
├── pnpm-lock.yaml
├── src/
│   ├── main.tsx              # React entry
│   ├── canvas/
│   │   ├── Canvas.tsx        # Top-level surface, mode router
│   │   ├── modes/            # Conversation / File / Code / Doc / ...
│   │   └── intent/           # Input bar, voice, drag-drop
│   ├── a2ui/                 # A2UI renderer + component catalog
│   └── lib/                  # Shared utilities
├── src-tauri/
│   ├── Cargo.toml
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
- @ai-sdk/react (streaming)
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
