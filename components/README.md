# A2UI Component Catalog

The vetted component catalog the Praxis agent renders onto the Canvas.

## Status

Placeholder. Component catalog lands with milestone **M0.5** — see [Roadmap](../docs/ROADMAP.md).

## Design principle

The agent composes UI declaratively from this catalog — it never emits arbitrary HTML or CSS. This guarantees security (no code injection), accessibility (every component is WCAG-compliant), and visual consistency (every render looks like Praxis). Wire format: **A2UI v0.9**.

## Component catalog v1

### File

| Component | Purpose |
|---|---|
| `<FileTree>` | Directory tree navigation |
| `<FileGrid>` | Thumbnail grid view |
| `<FilePreview>` | Single-file preview (image, PDF, text) |
| `<DiffView>` | Side-by-side or unified diff |

### Code

| Component | Purpose |
|---|---|
| `<CodeEditor language=… file=…>` | Full editor with syntax highlighting |
| `<TerminalPane>` | Embedded sandboxed terminal |
| `<TestRunnerPanel>` | Test results with pass/fail tree |

### Document

| Component | Purpose |
|---|---|
| `<RichTextEditor>` | WYSIWYG prose editor |
| `<MarkdownEditor>` | Split-pane Markdown editor |
| `<OutlinePanel>` | Document structure navigator |
| `<CommentThread>` | Inline comment threads |

### Data

| Component | Purpose |
|---|---|
| `<Table editable>` | Editable data table |
| `<Chart kind="line\|bar\|…">` | Charting (line, bar, pie, scatter) |
| `<KPIBoard>` | Key metric cards |

### System

| Component | Purpose |
|---|---|
| `<SettingsForm schema=…>` | Schema-driven settings panel |
| `<NetworkPanel>` | Network interfaces and status |
| `<ProcessMonitor>` | Running processes and resource use |

### Generic

| Component | Purpose |
|---|---|
| `<Form>` | Schema-driven form |
| `<Stepper>` | Multi-step wizard |
| `<Cards>` | Card grid layout |
| `<Map>` | Geographic map |
| `<Image>` | Image viewer |
| `<Video>` | Video player |

## Planned structure (M0.5)

```
components/
├── catalog.json          # Machine-readable component registry
├── src/
│   └── praxis_components/
│       ├── file/
│       ├── code/
│       ├── document/
│       ├── data/
│       ├── system/
│       └── generic/
└── storybook/            # Visual component browser
```
