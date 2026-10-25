import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from praxis_agent.memory import TurnSummary
from praxis_agent.tools.files import Entry, FileText, Listing
from praxis_agent.ui.a2ui import MAX_BLOCKS, MAX_CARDS, build_messages, build_trail
from praxis_agent.ui.plan import (
    Card,
    CardsBlock,
    CodeBlock,
    FormBlock,
    FormField,
    Screen,
    TextBlock,
)
from sample_plan import SAMPLE_SCREEN

# Also read by canvas/src/a2ui/contract.test.tsx, which feeds them to the real renderer.
GOLDEN = Path(__file__).parent / "golden" / "workspace_messages.json"
TRAIL_GOLDEN = Path(__file__).parent / "golden" / "trail_messages.json"

SAMPLE_TURNS = [
    TurnSummary(n=1, label="plan a project", title="Plan a project", mode="conversation"),
    TurnSummary(n=2, label="show my notes", title="notes.md", mode="file"),
]


def screen(*blocks, **fields):
    return Screen(mode="conversation", title="t", blocks=list(blocks), **fields)


def build(a_screen):
    return build_messages(a_screen, timezone=UTC)


def components_of(messages):
    (update,) = [m["updateComponents"] for m in messages if "updateComponents" in m]
    return {c["id"]: c for c in update["components"]}


def child_refs(component):
    refs = list(component.get("children", []))
    if "child" in component:
        refs.append(component["child"])
    return refs


def matches_golden(messages, golden):
    if os.environ.get("UPDATE_GOLDEN"):
        golden.parent.mkdir(exist_ok=True)
        text = json.dumps(messages, indent=2, ensure_ascii=False) + "\n"
        golden.write_bytes(text.encode("utf-8"))
    return messages == json.loads(golden.read_text(encoding="utf-8"))


def test_matches_golden():
    assert matches_golden(build(SAMPLE_SCREEN), GOLDEN)


def test_trail_matches_golden():
    assert matches_golden(build_trail(SAMPLE_TURNS, current=2), TRAIL_GOLDEN)


def test_the_trail_lists_turns_in_order_and_marks_the_one_on_screen():
    shown = components_of(build_trail(SAMPLE_TURNS, current=1))

    assert shown["root"]["children"] == ["trail_head", "t1", "t2"]
    assert shown["t1_label"]["text"] == "▸ plan a project"
    assert shown["t2_label"]["text"] == "show my notes"
    assert shown["t2"]["action"]["event"] == {"name": "revisit", "context": {"turn": 2}}
    # Requests are shown as typed, never read as Markdown.
    assert {shown[f"t{n}_label"]["variant"] for n in (1, 2)} == {"h5"}


def test_replaces_the_surface_before_populating_it():
    kinds = [next(k for k in m if k != "version") for m in build(SAMPLE_SCREEN)]
    assert kinds == ["deleteSurface", "createSurface", "updateComponents", "updateDataModel"]


def test_ids_are_unique_and_every_reference_resolves_from_root():
    messages = build(SAMPLE_SCREEN)
    (update,) = [m["updateComponents"] for m in messages if "updateComponents" in m]
    ids = [c["id"] for c in update["components"]]
    assert len(ids) == len(set(ids))

    components = components_of(messages)
    reachable, queue = set(), ["root"]
    while queue:
        current = queue.pop()
        reachable.add(current)
        queue.extend(child_refs(components[current]))  # KeyError means a dangling reference
    assert reachable == set(ids)


def test_form_bindings_exist_in_the_data_model():
    messages = build(SAMPLE_SCREEN)
    components = components_of(messages)
    (data,) = [m["updateDataModel"]["value"] for m in messages if "updateDataModel" in m]

    assert data == {"b1": {"project_name": "", "goal": ""}}
    assert components["b1_f_project_name"]["value"] == {"path": "/b1/project_name"}
    assert components["b1_submit"]["action"]["event"] == {
        "name": "submit",
        "context": {"purpose": "Gather project details", "values": {"path": "/b1"}},
    }


def test_cards_carry_their_intent_as_an_action():
    components = components_of(build(SAMPLE_SCREEN))
    assert components["b2_c0"]["action"]["event"] == {
        "name": "intent",
        "context": {"text": "Show me an example project plan"},
    }


def test_no_action_context_would_be_read_as_a_data_binding():
    # A2UI treats an object with a `path` or `call` key as a binding or a function
    # call, so a context shaped like that arrives at the agent empty.
    contexts = [
        c["action"]["event"]["context"]
        for c in components_of(build(SAMPLE_SCREEN)).values()
        if "action" in c
    ]
    assert len(contexts) >= 5
    assert not any({"path", "call"} & set(context) for context in contexts)


# --- code ------------------------------------------------------------------


def code_text(code, language="python"):
    block = CodeBlock(kind="code", language=language, code=code)
    return components_of(build(screen(block)))["b0"]["text"]


def test_code_becomes_one_fenced_block():
    assert code_text("x = 1\n\ny = 2") == "```python\nx = 1\n\ny = 2\n```"


def test_code_the_model_already_fenced_is_not_fenced_twice():
    assert code_text("```python\nx = 1\n```") == "```python\nx = 1\n```"


def test_code_containing_backticks_cannot_close_its_fence():
    text = code_text("echo ```not a fence```\n```", language="bash")
    assert text.startswith("````bash\n") and text.endswith("\n````")


def test_code_language_cannot_inject_markdown():
    text = code_text("x", language="python\n```\n# injected")
    assert text.startswith("```python#injected\n")


# --- files -----------------------------------------------------------------


def entry(name, kind="file", size=10, modified=datetime(2026, 9, 29, 14, 5, tzinfo=UTC)):
    return Entry(
        name=name,
        path=f"/home/ada/{name}",
        kind=kind,
        size=None if kind == "directory" else size,
        modified=modified,
    )


def listing(*entries, total=None, truncated=False, pattern=None):
    return Listing(
        directory="/home/ada",
        pattern=pattern,
        entries=list(entries),
        total=len(entries) if total is None else total,
        truncated=truncated,
    )


def test_each_listed_entry_opens_what_it_names():
    components = components_of(build(screen(listing(entry("docs", "directory"), entry("a.txt")))))

    assert components["f0_e0"]["action"]["event"] == {
        "name": "open",
        "context": {"target": "/home/ada/docs", "kind": "directory"},
    }
    assert components["f0_e1"]["action"]["event"]["context"] == {
        "target": "/home/ada/a.txt",
        "kind": "file",
    }
    assert components["f0_e0_name"]["text"] == "docs/"
    assert components["f0_e0_meta"]["text"] == "folder"
    assert components["f0_e1_meta"]["text"] == "10 bytes · 2026-09-29 14:05"


def test_file_names_are_never_read_as_markdown():
    components = components_of(build(screen(listing(entry("__init__.py"), entry("*draft*.md")))))

    # Only the body variant goes through the Markdown renderer.
    assert components["f0_e0_name"] == {
        "id": "f0_e0_name",
        "component": "Text",
        "text": "__init__.py",
        "variant": "h5",
    }
    assert components["f0_e1_name"]["variant"] == "h5"
    assert components["f0_head"]["variant"] == "caption"


def test_file_times_are_shown_in_the_given_timezone():
    plus_five_thirty = timezone(timedelta(hours=5, minutes=30))
    messages = build_messages(screen(listing(entry("a.txt"))), timezone=plus_five_thirty)

    assert components_of(messages)["f0_e0_meta"]["text"] == "10 bytes · 2026-09-29 19:35"


def test_listing_heads_say_what_is_listed():
    folder = components_of(build(screen(listing(entry("a.txt")))))
    search = components_of(build(screen(listing(entry("a.py"), entry("b.py"), pattern="*.py"))))

    assert folder["f0_head"]["text"] == "/home/ada · 1 item"
    assert search["f0_head"]["text"] == "2 files matching “*.py” in /home/ada"


def test_search_rows_say_which_subfolder_a_file_is_in():
    deep = entry("helpers.py").model_copy(update={"path": "/home/ada/code/utils/helpers.py"})
    found = components_of(build(screen(listing(deep, pattern="*.py"))))
    listed = components_of(build(screen(listing(deep))))

    assert found["f0_e0_name"]["text"] == "code/utils/helpers.py"
    assert listed["f0_e0_name"]["text"] == "helpers.py"
    assert (
        found["f0_e0"]["action"]["event"]["context"]["target"] == "/home/ada/code/utils/helpers.py"
    )


def test_paths_under_the_home_folder_are_written_with_a_tilde():
    home = Path.home()
    in_home = Listing(directory=str(home / "Documents"), entries=[], total=0, truncated=False)
    at_home = Listing(directory=str(home), entries=[], total=0, truncated=False)
    file = FileText(path=str(home / "notes.txt"), size=1, content="x", truncated=False)

    shown = components_of(build(screen(in_home, at_home, file)))

    assert shown["f0_head"]["text"] == "~/Documents · 0 items"
    assert shown["f1_head"]["text"] == "~ · 0 items"
    assert shown["f2_head"]["text"] == "~/notes.txt · 1 byte"


def test_a_listing_that_was_cut_short_says_so():
    components = components_of(build(screen(listing(entry("a.txt"), total=500, truncated=True))))

    assert components["f0_more"]["text"] == "Showing the first 1."
    assert components["f0"]["children"][-1] == "f0_more"


def test_an_empty_listing_has_no_rows_and_no_note():
    components = components_of(build(screen(listing())))

    assert components["f0"]["children"] == ["f0_head"]
    assert components["f0_head"]["text"] == "/home/ada · 0 items"


def file_components(content, *, path="/home/ada/notes.md", truncated=False, size=None):
    size = len(content) if size is None else size
    file = FileText(path=path, size=size, content=content, truncated=truncated)
    return components_of(build(screen(file)))


def test_a_file_is_shown_as_it_is_on_disk_inside_a_fence():
    components = file_components("# Title\n\n*not* rendered\n")

    assert components["f0_text"]["text"] == "```md\n# Title\n\n*not* rendered\n\n```"
    assert components["f0_head"]["text"] == "/home/ada/notes.md · 24 bytes"
    assert components["f0"]["children"] == ["f0_head", "f0_text"]


def test_a_file_that_is_one_fenced_block_is_not_unwrapped():
    content = "```python\nx = 1\n```"

    assert file_components(content)["f0_text"]["text"] == f"````md\n{content}\n````"


def test_a_cut_off_file_and_an_empty_file_say_so():
    cut = file_components("abc", truncated=True, size=9000)
    empty = file_components("")

    assert cut["f0_more"]["text"] == "This is only the beginning of the file."
    assert empty["f0_more"]["text"] == "The file is empty."
    assert "f0_text" not in empty


def test_the_folder_above_is_offered_when_there_is_one():
    with_up = components_of(build(screen(listing(), up="/home")))
    without = components_of(build(screen(listing())))

    assert with_up["root"]["children"] == ["title", "up", "f0"]
    assert with_up["up"]["action"]["event"]["context"] == {"target": "/home", "kind": "directory"}
    assert with_up["up_label"]["text"] == "↑ home"
    assert "up" not in without


# --- bounds ----------------------------------------------------------------


def test_duplicate_and_unusable_field_names_get_distinct_bindings():
    form = FormBlock(
        kind="form",
        purpose="p",
        fields=[
            FormField(name="Name", label="a"),
            FormField(name="name", label="b"),
            FormField(name="???", label="c"),
        ],
    )
    (data,) = [m["updateDataModel"]["value"] for m in build(screen(form)) if "updateDataModel" in m]
    assert list(data["b0"]) == ["name", "name_2", "field"]


def test_oversized_screens_are_truncated():
    cards = CardsBlock(kind="cards", items=[Card(title=str(i), intent="x") for i in range(50)])
    texts = [TextBlock(kind="text", text=str(i)) for i in range(50)]

    components = components_of(build(screen(cards, *texts)))

    assert len(components["root"]["children"]) == 1 + MAX_BLOCKS  # title + blocks
    assert len(components["b0"]["children"]) == MAX_CARDS


def test_no_data_model_message_without_a_form():
    messages = build(screen(TextBlock(kind="text", text="hi")))
    assert not any("updateDataModel" in m for m in messages)
