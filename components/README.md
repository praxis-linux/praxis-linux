# A2UI Component Catalog

The vetted component catalog the Praxis agent renders onto the Canvas.

## Status

The catalog that exists today lives in [`canvas/src/a2ui/catalog.tsx`](../canvas/src/a2ui/catalog.tsx): a vetted subset of the A2UI basic catalog (Text, Row, Column, List, Card, Tabs, Divider, Modal, Button, TextField, CheckBox, ChoicePicker, Slider, DateTimeInput), and two components of Praxis's own:

- **FileImage** shows an image file from inline data only, so showing an image never fetches anything.
- **CodeEditor** is a CodeMirror 6 editor bound to the A2UI data model: what the user types is what Save sends.

Proposed changes are shown as diffs in a `praxis-diff` code block, which only the agent can produce. See [ADR-003](../docs/architecture/ADR-003-ui-wire-format.md).

The components below are planned. `<CodeEditor>` is built, as above; `<FilePreview>` and `<DiffView>` exist in a simpler form (text and image previews; a unified diff in a code block).

## Design principle

The agent composes UI declaratively from this catalog — it never emits arbitrary HTML or CSS. The aims are security (no code injection), accessibility (every component WCAG-compliant), and visual consistency (every render looks like Praxis). Wire format: **A2UI v0.9**.

## Planned Praxis components

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

## Planned structure

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
