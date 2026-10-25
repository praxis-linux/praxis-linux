"""Compile a Screen into A2UI v0.9 messages for the Praxis catalog.

Everything the protocol requires structurally — unique component ids, a `root`,
references that resolve, bindings that exist in the data model — is guaranteed
here rather than asked of the model.
"""

import re
from datetime import tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..tools.files import MAX_EDIT_BYTES, Entry, FileText, ImageFile, Listing, human_size
from ..tools.run import RunResult
from .plan import (
    CardsBlock,
    Changed,
    CodeBlock,
    Editor,
    FormBlock,
    Proposal,
    Ran,
    Screen,
    TextBlock,
)

if TYPE_CHECKING:
    # memory imports ui.plan, so importing it here at runtime would be circular.
    from ..memory import TurnSummary

VERSION = "v0.9"
# Must match PRAXIS_CATALOG_ID in canvas/src/a2ui/catalog.tsx.
PRAXIS_CATALOG_ID = "praxis-linux:catalog:phase0"
WORKSPACE_SURFACE = "workspace"
CONTEXT_SURFACE = "context"

# Bounds on what one screen may hold, whatever the model returns.
MAX_BLOCKS = 12
MAX_CARDS = 8
MAX_FIELDS = 8
# How much of a request the conversation trail shows.
TRAIL_LABEL_CHARS = 80

_TEXT_VARIANT = {"heading": "h3", "body": "body", "caption": "caption"}

# Blocks the agent makes rather than the model: what was looked up, opened or run.
_AGENT_MADE = (Listing, FileText, ImageFile, Editor, Proposal, Changed, Ran)

# The editor's language, by file suffix. The Canvas shows anything else as plain text.
_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".rs": "rust",
    ".sh": "shell",
}

Message = dict[str, Any]
Component = dict[str, Any]


def build_messages(
    screen: Screen, surface_id: str = WORKSPACE_SURFACE, *, timezone: tzinfo | None = None
) -> list[Message]:
    """Messages that replace `surface_id` with `screen`.

    File times are shown in `timezone`; the machine's own when it is not given.
    """
    return replace(surface_id, *compile_screen(screen, timezone=timezone))


def replace(surface_id: str, components: list[Component], data: dict[str, Any]) -> list[Message]:
    """Messages that throw away whatever `surface_id` shows and draw `components` instead."""
    return [
        # Deleting a surface that does not exist is a no-op, so the agent can
        # replace the workspace without tracking what the canvas is showing.
        {"version": VERSION, "deleteSurface": {"surfaceId": surface_id}},
        {
            "version": VERSION,
            "createSurface": {"surfaceId": surface_id, "catalogId": PRAXIS_CATALOG_ID},
        },
        {
            "version": VERSION,
            "updateComponents": {"surfaceId": surface_id, "components": components},
        },
        *(
            [
                {
                    "version": VERSION,
                    "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data},
                }
            ]
            if data
            else []
        ),
    ]


def update(
    surface_id: str, components: list[Component], data_changes: dict[str, Any]
) -> list[Message]:
    """Messages that redraw `surface_id` in place, keeping what the user sees steady.

    Components are matched by id. Only use this when every component already on the
    surface is among `components`: one that is not would be left unreachable from the
    root, which the Canvas rejects. `data_changes` holds the forms that are new or
    changed; each is set on its own, leaving what the user typed into the others.
    """
    return [
        {
            "version": VERSION,
            "updateComponents": {"surfaceId": surface_id, "components": components},
        },
        *(
            {
                "version": VERSION,
                "updateDataModel": {"surfaceId": surface_id, "path": f"/{key}", "value": value},
            }
            for key, value in data_changes.items()
        ),
    ]


def compile_screen(
    screen: Screen, *, timezone: tzinfo | None = None
) -> tuple[list[Component], dict[str, Any]]:
    """The components that draw `screen`, and the data model its forms and editors bind to.

    The model's blocks get ids `b0`, `b1`, ... and what was looked up gets `f0`,
    `f1`, ...: a listing keeps its id while the reply around it is still being
    written, so the screen can be redrawn in place as it streams.
    """
    components: list[Component] = [_plain("title", screen.title, "h2")]
    root_children = ["title"]
    data: dict[str, Any] = {}

    if screen.up:
        components.extend(_open_button("up", f"↑ {Path(screen.up).name or screen.up}", screen.up))
        root_children.append("up")

    replies = found = 0
    for block in screen.blocks[:MAX_BLOCKS]:
        if isinstance(block, _AGENT_MADE):
            block_id, found = f"f{found}", found + 1
        else:
            block_id, replies = f"b{replies}", replies + 1
        root_children.append(block_id)
        if isinstance(block, TextBlock):
            components.append(_text(block_id, block))
        elif isinstance(block, CodeBlock):
            components.append(_code(block_id, block))
        elif isinstance(block, CardsBlock):
            components.extend(_cards(block_id, block))
        elif isinstance(block, FormBlock):
            form_components, initial = _form(block_id, block)
            components.extend(form_components)
            data[block_id] = initial
        elif isinstance(block, Listing):
            components.extend(_listing(block_id, block, timezone))
        elif isinstance(block, FileText):
            components.extend(_file(block_id, block, _runnable(block.path, screen.can_run)))
        elif isinstance(block, ImageFile):
            components.extend(_image(block_id, block))
        elif isinstance(block, Editor):
            editor, initial = _editor(block_id, block, _runnable(block.path, screen.can_run))
            components.extend(editor)
            data[block_id] = initial
        elif isinstance(block, Proposal):
            components.extend(_proposal(block_id, block))
        elif isinstance(block, Changed):
            components.extend(_changed(block_id, block))
        elif isinstance(block, Ran):
            components.extend(
                _ran(block_id, block.result, _runnable(block.result.path, screen.can_run))
            )

    components.insert(0, {"id": "root", "component": "Column", "children": root_children})
    return components, data


def build_trail(turns: "list[TurnSummary]", current: int | None = None) -> list[Message]:
    """Messages that draw the conversation so far on the context surface.

    Each turn is a row that brings its screen back; `current` is the one on screen.
    """
    components: list[Component] = [_plain("trail_head", "This conversation", "caption")]
    children = ["trail_head"]
    for turn in turns:
        row_id = f"t{turn.n}"
        children.append(row_id)
        label = turn.label
        if len(label) > TRAIL_LABEL_CHARS:
            label = label[: TRAIL_LABEL_CHARS - 1].rstrip() + "…"
        if turn.n == current:
            label = f"▸ {label}"
        components += [
            _plain(f"{row_id}_label", label, "h5"),
            _plain(f"{row_id}_title", turn.title, "caption"),
            {
                "id": f"{row_id}_body",
                "component": "Column",
                "children": [f"{row_id}_label", f"{row_id}_title"],
            },
            {
                "id": row_id,
                "component": "Button",
                "variant": "borderless",
                "child": f"{row_id}_body",
                "action": {"event": {"name": "revisit", "context": {"turn": turn.n}}},
            },
        ]
    components.insert(0, {"id": "root", "component": "Column", "children": children})
    return replace(CONTEXT_SURFACE, components, {})


def _plain(component_id: str, text: str, variant: str) -> Component:
    """Text the renderer shows as written. Only the `body` variant is read as Markdown,
    which would turn a name like `__init__.py` into bold text."""
    assert variant != "body"
    return {"id": component_id, "component": "Text", "text": text, "variant": variant}


def _text(block_id: str, block: TextBlock) -> Component:
    return {
        "id": block_id,
        "component": "Text",
        "text": block.text,
        "variant": _TEXT_VARIANT[block.style],
    }


def _code(block_id: str, block: CodeBlock) -> Component:
    code = block.code.strip("\n")
    # Models often fence the code themselves; keep only what is inside.
    fenced = re.fullmatch(r"```[^\n]*\n(.*?)\n?```", code, flags=re.DOTALL)
    if fenced:
        code = fenced.group(1)
    return _fenced(block_id, code, block.language)


def _fenced(component_id: str, code: str, language: str) -> Component:
    """A body Text holding `code` as one fenced Markdown code block."""
    # The fence must be longer than any run of backticks in the code.
    longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
    fence = "`" * max(3, longest + 1)
    language = re.sub(r"[^A-Za-z0-9_+#.-]", "", language)
    return {
        "id": component_id,
        "component": "Text",
        "text": f"{fence}{language}\n{code}\n{fence}",
        "variant": "body",
    }


def _cards(block_id: str, block: CardsBlock) -> list[Component]:
    components: list[Component] = []
    card_ids: list[str] = []
    for j, card in enumerate(block.items[:MAX_CARDS]):
        card_id = f"{block_id}_c{j}"
        card_ids.append(card_id)
        body = [f"{card_id}_title"]
        components.append(_plain(f"{card_id}_title", card.title, "h4"))
        if card.description:
            body.append(f"{card_id}_desc")
            components.append(_plain(f"{card_id}_desc", card.description, "caption"))
        components.append({"id": f"{card_id}_body", "component": "Column", "children": body})
        components.append(
            {
                "id": card_id,
                "component": "Button",
                "child": f"{card_id}_body",
                "action": {"event": {"name": "intent", "context": {"text": card.intent}}},
            }
        )
    components.append({"id": block_id, "component": "Column", "children": card_ids})
    return components


def _form(block_id: str, block: FormBlock) -> tuple[list[Component], dict[str, str]]:
    components: list[Component] = []
    children: list[str] = []
    initial: dict[str, str] = {}
    for field in block.fields[:MAX_FIELDS]:
        name = _unique(_slug(field.name), initial)
        initial[name] = ""
        field_id = f"{block_id}_f_{name}"
        children.append(field_id)
        components.append(
            {
                "id": field_id,
                "component": "TextField",
                "label": field.label,
                "value": {"path": f"/{block_id}/{name}"},
                "variant": "longText" if field.multiline else "shortText",
            }
        )
    components.append(
        {"id": f"{block_id}_submit_label", "component": "Text", "text": block.submit_label}
    )
    components.append(
        {
            "id": f"{block_id}_submit",
            "component": "Button",
            "variant": "primary",
            "child": f"{block_id}_submit_label",
            "action": {
                "event": {
                    "name": "submit",
                    "context": {"purpose": block.purpose, "values": {"path": f"/{block_id}"}},
                }
            },
        }
    )
    children.append(f"{block_id}_submit")
    components.append({"id": block_id, "component": "Column", "children": children})
    return components, initial


def _listing(block_id: str, listing: Listing, timezone: tzinfo | None) -> list[Component]:
    """A folder's contents or a search result. Every row opens what it names."""
    where = _tilde(listing.directory)
    if listing.pattern is None:
        head = f"{where} · {_count(listing.total, 'item')}"
    else:
        head = f"{_count(listing.total, 'file')} matching “{listing.pattern}” in {where}"
    components = [_plain(f"{block_id}_head", head, "caption")]
    children = [f"{block_id}_head"]

    for j, entry in enumerate(listing.entries):
        row_id = f"{block_id}_e{j}"
        children.append(row_id)
        # A search reaches into subfolders, so its rows say where each file is.
        name = entry.name if listing.pattern is None else _below(entry.path, listing.directory)
        components.extend(_entry_row(row_id, entry, name, timezone))

    shown = len(listing.entries)
    if listing.total > shown or listing.truncated:
        note = f"Showing the first {shown}." if shown else "Too many files to search in one go."
        components.append(_plain(f"{block_id}_more", note, "caption"))
        children.append(f"{block_id}_more")

    components.append({"id": block_id, "component": "Column", "children": children})
    return components


def _entry_row(row_id: str, entry: Entry, name: str, timezone: tzinfo | None) -> list[Component]:
    if entry.kind == "directory":
        name, meta = f"{name}/", "folder"
    else:
        modified = entry.modified.astimezone(timezone)
        meta = f"{human_size(entry.size)} · {modified:%Y-%m-%d %H:%M}"
    return [
        _plain(f"{row_id}_name", name, "h5"),
        _plain(f"{row_id}_meta", meta, "caption"),
        {
            "id": f"{row_id}_row",
            "component": "Row",
            "children": [f"{row_id}_name", f"{row_id}_meta"],
            "justify": "spaceBetween",
            "align": "center",
        },
        {
            "id": row_id,
            "component": "Button",
            "variant": "borderless",
            "child": f"{row_id}_row",
            "action": _open(entry.path, entry.kind),
        },
    ]


def _open_button(component_id: str, label: str, path: str) -> list[Component]:
    """A button that opens the folder at `path`."""
    return [
        _plain(f"{component_id}_label", label, "h5"),
        {
            "id": component_id,
            "component": "Button",
            "variant": "borderless",
            "child": f"{component_id}_label",
            "action": _open(path, "directory"),
        },
    ]


def _open(path: str, kind: str) -> dict[str, Any]:
    # The key is `target`, not `path`: A2UI reads any object with a `path` key as a
    # data binding, so a context of {"path": ...} reaches the agent empty.
    return {"event": {"name": "open", "context": {"target": path, "kind": kind}}}


def _file(block_id: str, file: FileText, runnable: bool) -> list[Component]:
    """A file's text, shown as it is on disk, with buttons to edit or run it."""
    head = f"{_tilde(file.path)} · {human_size(file.size)}"
    components = [_plain(f"{block_id}_head", head, "caption")]
    children = [f"{block_id}_head"]
    buttons = []
    # A file read in part may still be short enough to edit; opening it reads it whole.
    if file.sha256 is not None or file.size <= MAX_EDIT_BYTES:
        buttons.append((f"{block_id}_edit", "Edit", "edit", {"target": file.path}, "default"))
    if runnable:
        buttons.append((f"{block_id}_run", "Run", "run", {"target": file.path}, "default"))
    if buttons:
        components.extend(_buttons(f"{block_id}_actions", buttons))
        children.append(f"{block_id}_actions")
    if file.content:
        components.append(_fenced(f"{block_id}_text", file.content, Path(file.path).suffix[1:]))
        children.append(f"{block_id}_text")
    if file.truncated or not file.content:
        note = "This is only the beginning of the file." if file.content else "The file is empty."
        components.append(_plain(f"{block_id}_more", note, "caption"))
        children.append(f"{block_id}_more")
    components.append({"id": block_id, "component": "Column", "children": children})
    return components


def _image(block_id: str, image: ImageFile) -> list[Component]:
    """An image, carried inline: the Canvas never fetches anything to show it."""
    return [
        _plain(f"{block_id}_head", f"{_tilde(image.path)} · {human_size(image.size)}", "caption"),
        {
            "id": f"{block_id}_image",
            "component": "FileImage",
            "src": f"data:{image.media_type};base64,{image.data}",
            "description": Path(image.path).name,
        },
        {
            "id": block_id,
            "component": "Column",
            "children": [f"{block_id}_head", f"{block_id}_image"],
        },
    ]


def _editor(
    block_id: str, editor: Editor, runnable: bool
) -> tuple[list[Component], dict[str, str]]:
    """A file open for editing. Save sends what is in the editor, and the hash the
    file had when it was opened, which the write checks."""
    size = human_size(len(editor.content.encode("utf-8")))
    content = {"path": f"/{block_id}/content"}
    buttons = [
        (
            f"{block_id}_save",
            "Save",
            "save",
            {"target": editor.path, "base": editor.sha256, "content": content},
            "primary",
        )
    ]
    if runnable:
        buttons.append((f"{block_id}_run", "Run", "run", {"target": editor.path}, "default"))
    components = [
        _plain(f"{block_id}_head", f"{_tilde(editor.path)} · {size}", "caption"),
        {
            "id": f"{block_id}_code",
            "component": "CodeEditor",
            "value": content,
            "language": _LANGUAGES.get(Path(editor.path).suffix.lower(), ""),
            "label": Path(editor.path).name,
        },
        *_buttons(f"{block_id}_actions", buttons),
    ]
    children = [f"{block_id}_head", f"{block_id}_code", f"{block_id}_actions"]
    if runnable:
        note = "Run runs the file as saved, in a sandbox without network access."
        components.append(_plain(f"{block_id}_note", note, "caption"))
        children.append(f"{block_id}_note")
    components.append({"id": block_id, "component": "Column", "children": children})
    return components, {"content": editor.content}


def _proposal(block_id: str, proposal: Proposal) -> list[Component]:
    """A change the model proposed: nothing happens to the file unless the user applies it."""
    what = "Proposed new file" if proposal.created else "Proposed change to"
    context = {"proposal": proposal.id}
    note = "Nothing is changed until you apply it, and an applied change can be undone."
    return [
        _plain(f"{block_id}_head", f"{what} {_tilde(proposal.path)}", "caption"),
        _fenced(f"{block_id}_diff", proposal.diff, "diff"),
        *_buttons(
            f"{block_id}_actions",
            [
                (f"{block_id}_apply", "Apply", "apply", context, "primary"),
                (f"{block_id}_discard", "Discard", "discard", context, "default"),
            ],
        ),
        _plain(f"{block_id}_note", note, "caption"),
        {
            "id": block_id,
            "component": "Column",
            "children": [
                f"{block_id}_head",
                f"{block_id}_diff",
                f"{block_id}_actions",
                f"{block_id}_note",
            ],
        },
    ]


def _changed(block_id: str, changed: Changed) -> list[Component]:
    what = "Created" if changed.created else "Saved"
    return [
        _plain(f"{block_id}_text", f"{what} {_tilde(changed.path)}.", "caption"),
        *_buttons(
            f"{block_id}_actions",
            [(f"{block_id}_undo", "Undo", "undo", {"backup": changed.backup}, "default")],
        ),
        {
            "id": block_id,
            "component": "Row",
            "children": [f"{block_id}_text", f"{block_id}_actions"],
            "align": "center",
        },
    ]


def _ran(block_id: str, result: RunResult, runnable: bool) -> list[Component]:
    """What running a file printed, as it printed it."""
    if result.timed_out:
        how = "stopped: it took too long"
    else:
        how = f"exit code {result.exit_code}"
    head = f"$ {result.command} · {how} · {result.seconds:.1f} s"
    components = [_plain(f"{block_id}_head", head, "caption")]
    children = [f"{block_id}_head"]
    for name, label, text in (("out", "Output", result.stdout), ("err", "Errors", result.stderr)):
        if text:
            components.append(_plain(f"{block_id}_{name}_label", label, "caption"))
            components.append(_fenced(f"{block_id}_{name}", text.rstrip("\n"), ""))
            children += [f"{block_id}_{name}_label", f"{block_id}_{name}"]
    if not result.stdout and not result.stderr:
        components.append(_plain(f"{block_id}_quiet", "It printed nothing.", "caption"))
        children.append(f"{block_id}_quiet")
    if result.truncated:
        components.append(_plain(f"{block_id}_more", "The output was cut off.", "caption"))
        children.append(f"{block_id}_more")
    buttons = [(f"{block_id}_edit", "Edit", "edit", {"target": result.path}, "default")]
    if runnable:
        buttons.insert(
            0, (f"{block_id}_again", "Run again", "run", {"target": result.path}, "default")
        )
    components.extend(_buttons(f"{block_id}_actions", buttons))
    children.append(f"{block_id}_actions")
    components.append({"id": block_id, "component": "Column", "children": children})
    return components


def _buttons(
    row_id: str, buttons: list[tuple[str, str, str, dict[str, Any], str]]
) -> list[Component]:
    """A row of buttons, each (id, label, action name, action context, variant)."""
    components: list[Component] = []
    for button_id, label, name, context, variant in buttons:
        components.append({"id": f"{button_id}_label", "component": "Text", "text": label})
        components.append(
            {
                "id": button_id,
                "component": "Button",
                "variant": variant,
                "child": f"{button_id}_label",
                "action": {"event": {"name": name, "context": context}},
            }
        )
    components.append(
        {"id": row_id, "component": "Row", "children": [b[0] for b in buttons], "align": "center"}
    )
    return components


def _runnable(path: str, can_run: list[str]) -> bool:
    return Path(path).suffix.lower() in can_run


def _tilde(path: str) -> str:
    """`path` as it is usually written: with `~` for the user's home folder."""
    try:
        below = Path(path).relative_to(Path.home()).as_posix()
    except ValueError:
        return path
    return "~" if below == "." else f"~/{below}"


def _below(path: str, directory: str) -> str:
    """`path` relative to `directory`, which contains it."""
    try:
        return Path(path).relative_to(directory).as_posix()
    except ValueError:
        return path


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "field"


def _unique(name: str, taken: dict[str, str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}_{n}", n + 1
    return candidate
