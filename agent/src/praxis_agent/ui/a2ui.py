"""Compile a Screen into A2UI v0.9 messages for the Praxis catalog.

Everything the protocol requires structurally — unique component ids, a `root`,
references that resolve, bindings that exist in the data model — is guaranteed
here rather than asked of the model.
"""

import re
from datetime import tzinfo
from pathlib import Path
from typing import Any

from ..tools.files import Entry, FileText, Listing, human_size
from .plan import CardsBlock, CodeBlock, FormBlock, Screen, TextBlock

VERSION = "v0.9"
# Must match PRAXIS_CATALOG_ID in canvas/src/a2ui/catalog.tsx.
PRAXIS_CATALOG_ID = "praxis-linux:catalog:phase0"
WORKSPACE_SURFACE = "workspace"

# Bounds on what one screen may hold, whatever the model returns.
MAX_BLOCKS = 12
MAX_CARDS = 8
MAX_FIELDS = 8

_TEXT_VARIANT = {"heading": "h3", "body": "body", "caption": "caption"}

Message = dict[str, Any]
Component = dict[str, Any]


def build_messages(
    screen: Screen, surface_id: str = WORKSPACE_SURFACE, *, timezone: tzinfo | None = None
) -> list[Message]:
    """Messages that replace `surface_id` with `screen`.

    File times are shown in `timezone`; the machine's own when it is not given.
    """
    components: list[Component] = [_plain("title", screen.title, "h2")]
    root_children = ["title"]
    data: dict[str, Any] = {}

    if screen.up:
        components.extend(_open_button("up", f"↑ {Path(screen.up).name or screen.up}", screen.up))
        root_children.append("up")

    for i, block in enumerate(screen.blocks[:MAX_BLOCKS]):
        block_id = f"b{i}"
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
            components.extend(_file(block_id, block))

    components.insert(0, {"id": "root", "component": "Column", "children": root_children})

    messages: list[Message] = [
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
    ]
    if data:
        messages.append(
            {
                "version": VERSION,
                "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data},
            }
        )
    return messages


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


def _file(block_id: str, file: FileText) -> list[Component]:
    """A file's text, shown as it is on disk."""
    head = f"{_tilde(file.path)} · {human_size(file.size)}"
    components = [_plain(f"{block_id}_head", head, "caption")]
    children = [f"{block_id}_head"]
    if file.content:
        components.append(_fenced(f"{block_id}_text", file.content, Path(file.path).suffix[1:]))
        children.append(f"{block_id}_text")
    if file.truncated or not file.content:
        note = "This is only the beginning of the file." if file.content else "The file is empty."
        components.append(_plain(f"{block_id}_more", note, "caption"))
        children.append(f"{block_id}_more")
    components.append({"id": block_id, "component": "Column", "children": children})
    return components


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
