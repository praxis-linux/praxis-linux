# Canvas

The Praxis desktop shell. Tauri 2 + React 19 + TypeScript.

## Status

Scaffolded at **M0.5**, connected to the agent at **M0.6**, conversational since **M0.9**, with Code mode since **M0.11**. The Canvas renders A2UI v0.9 surfaces from the agent as they stream in, sends intents and UI actions back, and shows which model served each turn. Modes beyond what the agent can describe in text, code, cards and forms are not built yet — see the [Roadmap](../docs/ROADMAP.md).

## How it works

- The intent bar posts what you type to the agent.
- The agent answers with A2UI messages. `@a2ui/react` renders them; nothing outside the Praxis catalog is accepted.
- Clicking a card or submitting a form sends an A2UI action to the agent, which answers with the next screen.
- Files and folders the agent shows are rows you can click: a folder opens its listing, a file its text, an image the image itself (PNG, JPEG, GIF, WebP), carried inline. Those clicks never go through a model.
- A file's text comes with **Edit**, which opens it in a CodeMirror 6 editor, and **Run** where it can be run. **Save** writes what is in the editor; **Undo** takes it back. A change the agent proposes is shown as a diff, added lines green and removed lines red, with **Apply** and **Discard**. None of these go through a model.
- While the agent works, a status line says what it is doing, and the reply is drawn as the model writes it.
- The context rail shows the conversation so far. Clicking a turn shows its screen again.
- The conversation id is kept in `localStorage`, so a reload picks up where the conversation left off. "New" starts another conversation; "Delete…" asks, then deletes this one.
- **Esc stops the agent** while it works (the send button turns into a Stop button meanwhile). This is the kill switch: the turn is dropped and the agent ends every turn it has in progress.
- Every request that goes to a cloud model is announced with a notice, and marked ☁ in the intent bar.
- The welcome screen is rendered locally, so the Canvas opens without the agent.
- Links and images in the agent's text are shown as text: a link as its words and address, an image as its description. Nothing the agent writes can load a remote resource or take the window elsewhere.
- If the agent sends a screen the Canvas rejects, the previous screen stays and the rest of that reply is not drawn.
- **In the AppImage the Canvas starts its own agent** and model proxy, from a Python it carries, with a random token only the two of them know, and stops them when it closes (or dies). The window waits for the agent before resuming the conversation. See `scripts/build-appimage.sh`.

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
│   │   ├── CodeEditor.tsx    # The catalog's CodeMirror editor, bound to the data model
│   │   ├── welcome.ts        # The local welcome screen
│   │   └── contract.test.tsx # Agent contract, tested against the real renderer
│   ├── layers/               # WorkspaceLayer, ContextRail, IntentLayer
│   └── index.css             # Design tokens and A2UI theme
└── src-tauri/                # Rust shell, window and CSP configuration
    └── src/services.rs       # In the AppImage: starts the agent and the model proxy
```

## Setup

Requires Node 20+, pnpm (`corepack enable`) and, for the desktop window, the [Tauri prerequisites](https://tauri.app/start/prerequisites/). Start the [agent](../agent/README.md) first.

```bash
cd canvas
pnpm install
pnpm tauri dev     # desktop window
pnpm dev           # or just the web UI at http://localhost:1420
```

Set `VITE_PRAXIS_AGENT_URL` if the agent is not at `http://127.0.0.1:7729`. The Tauri CSP in `src-tauri/tauri.conf.json` names the agent address too.

## Checks

```bash
pnpm typecheck
pnpm test
pnpm build
```

`pnpm test` reads the golden files in `agent/tests/golden/`, which the agent's tests write.

## Planned

- shadcn/ui + Tailwind v4 for Praxis-specific components
- TipTap (Doc mode)
- Praxis components in the catalog: see [`components/`](../components/README.md)
