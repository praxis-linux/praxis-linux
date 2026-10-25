# Canvas

The Praxis desktop shell. Tauri 2 + React 19 + TypeScript.

## Status

Scaffolded at **M0.5**, connected to the agent at **M0.6**, conversational since **M0.9**. The Canvas renders A2UI v0.9 surfaces from the agent as they stream in, sends intents and UI actions back, and shows which model served each turn. Modes beyond what the agent can describe in text, code, cards and forms are not built yet — see the [Roadmap](../docs/ROADMAP.md).

## How it works

- The intent bar posts what you type to the agent.
- The agent answers with A2UI messages. `@a2ui/react` renders them; nothing outside the Praxis catalog is accepted.
- Clicking a card or submitting a form sends an A2UI action to the agent, which answers with the next screen.
- Files and folders the agent shows are rows you can click: a folder opens its listing, a file its text. Those clicks never go through a model.
- While the agent works, a status line says what it is doing, and the reply is drawn as the model writes it.
- The context rail shows the conversation so far. Clicking a turn shows its screen again.
- The conversation id is kept in `localStorage`, so a reload picks up where the conversation left off. "New" starts another conversation.
- The welcome screen is rendered locally, so the Canvas opens without the agent.

The contract with the agent is specified in [ADR-003](../docs/architecture/ADR-003-ui-wire-format.md).

## Structure

```
canvas/
├── src/
│   ├── main.tsx              # React entry
│   ├── App.tsx               # Layout: workspace, context rail, intent bar
│   ├── session.ts            # One conversation with the agent; owns the A2UI state
│   ├── agent/client.ts       # HTTP + NDJSON client for the agent
│   ├── a2ui/
│   │   ├── catalog.tsx       # The Praxis catalog: what the agent may render
│   │   ├── welcome.ts        # The local welcome screen
│   │   └── contract.test.tsx # Agent contract, tested against the real renderer
│   ├── layers/               # WorkspaceLayer, ContextRail, IntentLayer
│   └── index.css             # Design tokens and A2UI theme
└── src-tauri/                # Rust shell, window and CSP configuration
```

## Setup

Requires Node 20+, pnpm (`corepack enable`) and, for the desktop window, the [Tauri prerequisites](https://tauri.app/start/prerequisites/). Start the [agent](../agent/README.md) first.

```bash
cd canvas
pnpm install
pnpm tauri dev     # desktop window
pnpm dev           # or just the web UI at http://localhost:1420
```

Set `VITE_PRAXIS_AGENT_URL` if the agent is not at `http://127.0.0.1:8000`. The Tauri CSP in `src-tauri/tauri.conf.json` names the agent address too.

## Checks

```bash
pnpm typecheck
pnpm test
pnpm build
```

`pnpm test` reads the golden files in `agent/tests/golden/`, which the agent's tests write.

## Planned

- shadcn/ui + Tailwind v4 for Praxis-specific components
- CodeMirror 6 (Code mode), TipTap (Doc mode)
- Praxis components in the catalog: see [`components/`](../components/README.md)
