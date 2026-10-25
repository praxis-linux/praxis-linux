import asyncio
import base64
import json
import sqlite3
import threading
import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from praxis_fs.fs import Files
from praxis_fs.server import build_server
from pydantic_ai.models.function import FunctionModel

from praxis_agent import planner
from praxis_agent.audit import AuditLog
from praxis_agent.memory import Conversations
from praxis_agent.server import _Workspace, create_app
from praxis_agent.tools.files import FileTools
from praxis_agent.ui.plan import Screen, TextBlock
from sample_plan import PIXEL_PNG, SAMPLE_REPLY

SESSION = {"X-Praxis-Session": "test-session"}

# Where the Canvas reaches the agent; the agent refuses any other host name.
AGENT_URL = "http://127.0.0.1:7729"

REPLY = {"step": SAMPLE_REPLY.model_dump()}


def reply(*blocks, mode="file", title="Found"):
    return {"step": {"do": "reply", "mode": mode, "title": title, "blocks": list(blocks)}}


def text(content):
    return {"kind": "text", "text": content, "style": "body"}


class FakeModel:
    """Stands in for the LiteLLM proxy: records prompts, streams answers from a script.

    Each entry of `script` is one model step; when it runs out, the model replies
    with SAMPLE_REPLY. Answers arrive a few characters at a time, as a real model's do.
    """

    def __init__(self):
        self.requests = []
        self.aliases = []
        self.script = []
        self.fail = False
        # While set, the model stops after its first chunk and waits, like a slow model.
        self.stall = False

    def for_alias(self, alias):
        self.aliases.append(alias)
        return FunctionModel(stream_function=self._stream)

    async def _stream(self, messages, info):
        if self.fail:
            raise RuntimeError("proxy unreachable")
        self.requests.append(messages)
        answer = json.dumps(self.script.pop(0) if self.script else REPLY)
        for i in range(0, len(answer), 9):
            yield answer[i : i + 9]
            while self.stall:
                await asyncio.sleep(0.01)

    def prompt(self, index=-1):
        return self.requests[index][-1].parts[-1].content


@pytest.fixture(autouse=True)
def every_draft_is_drawn(monkeypatch):
    # The fake model answers faster than a person could watch; draw every draft anyway.
    monkeypatch.setattr(planner, "DRAFT_INTERVAL", 0)


@pytest.fixture
def home(tmp_path):
    root = tmp_path / "home"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "plan.md").write_bytes(b"# Plan\n")
    (root / "app.py").write_bytes(b"print('hi')\n")
    (root / ".env").write_bytes(b"SECRET=1")
    return root


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def audit(tmp_path):
    return AuditLog(tmp_path / "audit.db")


@pytest.fixture
def memory(tmp_path):
    return Conversations(tmp_path / "conversations.db")


@pytest.fixture
def make_client(audit, memory, model, home):
    """A client for a fresh agent process that shares the databases of the others."""

    @asynccontextmanager
    async def in_process_praxis_fs():
        async with Client(build_server(Files([home]))) as mcp_client:
            yield await FileTools.connect(mcp_client)

    def make():
        app = create_app(
            audit=audit,
            conversations=Conversations(memory.path),
            model_for=model.for_alias,
            connect_files=in_process_praxis_fs,
        )
        return TestClient(app, base_url=AGENT_URL)

    return make


@pytest.fixture
def client(make_client):
    with make_client() as test_client:
        yield test_client


def events(response):
    return [json.loads(line) for line in response.text.splitlines()]


def modes(response):
    return [e["mode"] for e in events(response) if e["type"] == "mode"]


def statuses(response):
    return [e["text"] for e in events(response) if e["type"] == "status"]


def a2ui(response):
    return [e["message"] for e in events(response) if e["type"] == "a2ui"]


def surface_of(message):
    (body,) = [value for key, value in message.items() if key != "version"]
    return body["surfaceId"]


def surfaces(response):
    """Replays a response's A2UI messages the way the Canvas does.

    Fails the moment a message would leave a component that cannot be reached from
    the root, which the Canvas rejects. Returns each surface's components by id.
    """
    state = {}
    for message in a2ui(response):
        if "deleteSurface" in message:
            state.pop(message["deleteSurface"]["surfaceId"], None)
        elif "createSurface" in message:
            state[message["createSurface"]["surfaceId"]] = {"components": {}, "data": {}}
        elif "updateComponents" in message:
            update = message["updateComponents"]
            components = state[update["surfaceId"]]["components"]  # KeyError: never created
            components.update({c["id"]: c for c in update["components"]})
            assert reachable(components) == set(components), "an orphaned component"
        elif "updateDataModel" in message:
            update = message["updateDataModel"]
            surface = state[update["surfaceId"]]
            if update["path"] == "/":
                surface["data"] = update["value"]
            else:
                surface["data"][update["path"].strip("/")] = update["value"]
    return state


def reachable(components):
    seen, queue = set(), ["root"]
    while queue:
        current = queue.pop()
        seen.add(current)
        component = components[current]
        queue.extend(component.get("children", []))
        if "child" in component:
            queue.append(component["child"])
    return seen


def workspace(response):
    return surfaces(response)["workspace"]["components"]


def trail(response):
    return surfaces(response)["context"]["components"]


def rows(audit, table):
    with sqlite3.connect(audit.path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY id")]


def action(name, context):
    return {
        "version": "v0.9",
        "action": {
            "name": name,
            "surfaceId": "workspace",
            "sourceComponentId": "b2_c0",
            "timestamp": "2026-09-30T00:00:00Z",
            "context": context,
        },
    }


def ask(client, intent, session=SESSION):
    return client.post("/intent", json={"intent": intent}, headers=session)


# --- a plain turn ----------------------------------------------------------


def test_a_turn_streams_its_call_then_draws_the_reply(client):
    response = ask(client, "plan a project")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    got = events(response)
    assert got[0] == {"type": "call", "model": "local-fast", "destination": "local"}
    assert got[1] == {"type": "mode", "mode": "conversation"}
    shown = workspace(response)
    assert shown["title"]["text"] == "Plan a project"
    assert shown["root"]["children"] == ["title", "b0", "b1", "b2", "b3"]
    assert shown["b3"]["text"] == '```python\nprint("hello")\nprint("world")\n```'


def test_the_reply_is_drawn_as_it_is_written_without_being_redrawn_from_scratch(client):
    response = ask(client, "plan a project")

    on_workspace = [m for m in a2ui(response) if surface_of(m) == "workspace"]
    replaced = [m for m in on_workspace if "deleteSurface" in m]
    drawn = [m for m in on_workspace if "updateComponents" in m]
    assert len(replaced) == 1
    assert len(drawn) > 10
    # The opening text was shown growing before the reply was complete.
    first_texts = [
        c["text"] for m in drawn for c in m["updateComponents"]["components"] if c["id"] == "b0"
    ]
    assert first_texts[0] != first_texts[-1]
    assert first_texts[-1] == "Tell me about the **project**."


def test_a_form_is_shown_only_when_whole_and_its_data_model_is_sent_once(client):
    response = ask(client, "plan a project")

    drawn = [m["updateComponents"]["components"] for m in a2ui(response) if "updateComponents" in m]
    form_fields = [
        sorted(c["id"] for c in components if c["id"].startswith("b1_f_"))
        for components in drawn
        if any(c["id"] == "b1" for c in components)
    ]
    assert form_fields and all(f == ["b1_f_goal", "b1_f_project_name"] for f in form_fields)
    data_models = [m for m in a2ui(response) if "updateDataModel" in m]
    assert len(data_models) == 1


def test_every_model_call_is_audited(client, audit):
    ask(client, "plan a project")

    (row,) = rows(audit, "model_calls")
    assert row["session_id"] == "test-session"
    assert row["model"] == "local-fast"
    assert row["destination"] == "local"
    assert row["purpose"] == "intent"
    assert row["prompt_chars"] == len("plan a project")
    assert row["status"] == "ok"
    assert row["requests"] == 1


def test_cloud_alias_is_reported_and_audited_as_cloud(client, audit, model, monkeypatch):
    monkeypatch.setenv("PRAXIS_MODEL", "cloud-strong")

    got = events(ask(client, "hi"))

    assert got[0] == {"type": "call", "model": "cloud-strong", "destination": "cloud"}
    assert model.aliases == ["cloud-strong"]
    assert rows(audit, "model_calls")[0]["destination"] == "cloud"


def test_no_model_call_when_the_audit_log_cannot_be_written(client, audit, model, monkeypatch):
    def broken(**_):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "begin", broken)

    got = events(ask(client, "hi"))

    assert [e["type"] for e in got] == ["error"]
    assert model.requests == []


def test_model_failure_is_reported_and_audited(client, audit, model, memory):
    model.fail = True

    got = events(ask(client, "hi"))

    assert [e["type"] for e in got] == ["call", "error"]
    (row,) = rows(audit, "model_calls")
    assert row["status"] == "error"
    assert "proxy unreachable" in row["error"]
    assert memory.turns("test-session") == []


def test_the_model_sees_paths_relative_to_the_main_folder(client, model, home):
    model.script = [{"step": {"do": "search_files", "pattern": "*.md"}}]

    ask(client, "my markdown files")

    assert "1 files matching '*.md' under the main folder:\ndocs/plan.md (" in model.prompt()
    assert str(home) not in model.prompt()
    assert str(home) not in model.requests[0][0].instructions


def test_a_screen_that_shows_files_is_in_file_mode(client, model):
    model.script = [{"step": {"do": "list_directory"}}]  # then SAMPLE_REPLY, a conversation

    assert modes(ask(client, "my files")) == ["file"]


# --- lookups the model asks for --------------------------------------------


def test_each_lookup_is_announced_before_it_runs(client, model):
    model.script = [
        {"step": {"do": "search_files", "pattern": "*.md", "directory": "docs"}},
        {"step": {"do": "recent_files", "days": 7}},
        {"step": {"do": "list_directory", "path": ""}},
    ]

    response = ask(client, "what changed?")

    assert statuses(response) == [
        "Searching for *.md in docs…",
        "Looking for files changed in the last 7 days…",
        "Opening your main folder…",
    ]
    kinds = [e["type"] for e in events(response)]
    assert kinds.index("status") < kinds.index("a2ui")


def test_a_lookup_is_run_shown_and_fed_back_to_the_model(client, model, home):
    model.script = [
        {"step": {"do": "recent_files", "days": 7, "pattern": "*.py"}},
        reply(text("One Python file changed this week.")),
    ]

    response = ask(client, "python files this week")

    # The model saw the real result before replying.
    assert "Result of recent_files:" in model.prompt()
    assert "app.py (12 bytes, modified " in model.prompt()
    # The Canvas shows the real result, not the model's account of it.
    shown = workspace(response)
    assert shown["root"]["children"] == ["title", "b0", "f0"]
    assert shown["f0_e0_name"]["text"] == "app.py"
    assert shown["f0_e0"]["action"]["event"]["context"]["target"] == str(home / "app.py")


def test_what_was_looked_up_goes_after_the_answer_and_before_the_cards(client, model):
    cards = {"kind": "cards", "items": [{"title": "More", "intent": "show more"}]}
    model.script = [
        {"step": {"do": "list_directory"}},
        reply(text("Here is your main folder."), cards),
    ]

    shown = workspace(ask(client, "my files"))

    assert shown["root"]["children"] == ["title", "b0", "f0", "b1"]
    assert shown["f0_head"]["text"].endswith("2 items")  # app.py and docs; .env is hidden
    assert shown["b1_c0"]["component"] == "Button"


def test_lookups_are_audited_as_asked_by_the_model(client, model, audit):
    model.script = [{"step": {"do": "read_text_file", "path": "app.py"}}]

    ask(client, "what is in app.py")

    (row,) = rows(audit, "tool_calls")
    assert row["tool"] == "read_text_file"
    assert json.loads(row["arguments"]) == {"path": "app.py"}
    assert row["asked_by"] == "model"
    assert row["status"] == "ok"
    assert rows(audit, "model_calls")[0]["requests"] == 2


def test_the_audit_log_records_the_tool_call_actually_made(client, model, audit, home):
    # A model that asks for files changed "in the last 0 days" still gets a day's worth,
    # and the log shows the call praxis-fs received, not the model's wording of it.
    (home / "old.txt").write_bytes(b"x")
    model.script = [
        {"step": {"do": "recent_files", "days": 0, "pattern": "*.py", "directory": ""}},
        {"step": {"do": "search_files", "pattern": "old"}},
    ]

    ask(client, "anything new?")

    recent, search = rows(audit, "tool_calls")
    assert recent["tool"] == search["tool"] == "search_files"
    assert json.loads(recent["arguments"]) == {
        "pattern": "*.py",
        "directory": "",
        "modified_within_days": 1,
    }
    assert json.loads(search["arguments"]) == {"pattern": "old", "directory": ""}


def test_a_search_that_finds_nothing_is_not_shown_and_the_model_is_told_why(client, model):
    model.script = [{"step": {"do": "search_files", "pattern": ".env"}}]

    response = ask(client, "find my .env")

    assert "0 files matching '.env' under the main folder." in model.prompt()
    assert "names start with a dot are usually left out" in model.prompt()
    assert not any(key.startswith("f") for key in workspace(response))
    assert modes(response) == ["conversation"]


def test_what_is_looked_up_twice_is_shown_once(client, model):
    model.script = [
        {"step": {"do": "list_directory", "path": "docs"}},
        {"step": {"do": "read_text_file", "path": "app.py"}},
        {"step": {"do": "list_directory", "path": "docs"}},
    ]

    shown = workspace(ask(client, "show me docs"))

    assert [c for c in shown["root"]["children"] if c.startswith("f")] == ["f0", "f1"]
    assert shown["f0_head"]["text"].endswith("docs · 1 item")
    assert "f1_text" in shown


def test_an_empty_folder_is_still_shown(client, model, home):
    (home / "empty").mkdir()
    model.script = [{"step": {"do": "list_directory", "path": "empty"}}]

    shown = workspace(ask(client, "open empty"))

    assert shown["f0_head"]["text"].endswith("0 items")


def test_a_refusal_is_stated_to_the_user_whatever_the_model_says(client, model, audit):
    model.script = [
        {"step": {"do": "read_text_file", "path": ".env"}},
        reply(text("There is no .env file."), mode="conversation"),  # what a small model says
    ]

    response = ask(client, "show my .env")

    assert "read_text_file was refused:" in model.prompt() and "hidden" in model.prompt()
    assert "SECRET" not in response.text
    shown = workspace(response)
    assert shown["root"]["children"] == ["title", "b0", "b1"]
    assert shown["b1"] == {
        "id": "b1",
        "component": "Text",
        "text": "Not read: .env is hidden, and Praxis does not read hidden files or folders.",
        "variant": "caption",
    }
    (row,) = rows(audit, "tool_calls")
    assert row["status"] == "error" and "hidden" in row["error"]


def test_a_lookup_that_merely_fails_is_not_announced(client, model):
    model.script = [
        {"step": {"do": "read_text_file", "path": "docs/missing.md"}},
        {"step": {"do": "search_files", "pattern": "plan"}},
        reply(text("Found it.")),
    ]

    response = ask(client, "read my plan")

    assert (
        "read_text_file failed: There is no file at docs/missing.md."
        in model.requests[1][-1].parts[-1].content
    )
    assert "Not read" not in response.text
    assert workspace(response)["f0_e0_name"]["text"] == "docs/plan.md"


def test_the_model_must_reply_after_three_lookups(client, model):
    model.script = [{"step": {"do": "list_directory"}}] * 3

    response = ask(client, "loop")

    assert len(model.requests) == 4
    assert workspace(response)["title"]["text"] == SAMPLE_REPLY.title


def test_no_file_is_read_when_the_audit_log_cannot_be_written(client, model, audit, monkeypatch):
    def broken(**_):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "begin_tool", broken)
    model.script = [{"step": {"do": "read_text_file", "path": "app.py"}}]

    response = ask(client, "read app.py")

    assert "audit log is unavailable" in model.prompt()
    assert "print('hi')" not in response.text


# --- drawing in place ------------------------------------------------------


def test_a_screen_with_fewer_parts_than_the_last_replaces_it():
    canvas = _Workspace()
    longer = Screen(
        mode="conversation",
        title="t",
        blocks=[TextBlock(kind="text", text="a"), TextBlock(kind="text", text="b")],
    )
    shorter = Screen(mode="conversation", title="t", blocks=[TextBlock(kind="text", text="a")])

    first, grown, shrunk = canvas.draw(shorter), canvas.draw(longer), canvas.draw(shorter)

    assert '"deleteSurface"' in first[1]
    assert not any('"deleteSurface"' in line for line in grown)
    assert '"deleteSurface"' in shrunk[0]


# --- browsing by clicking --------------------------------------------------


def test_opening_a_folder_lists_it_without_calling_a_model(client, model, audit, home):
    response = client.post(
        "/action",
        json=action("open", {"target": str(home / "docs"), "kind": "directory"}),
        headers=SESSION,
    )

    assert events(response)[0] == {"type": "mode", "mode": "file"}
    shown = workspace(response)
    assert shown["title"]["text"] == "docs"
    assert shown["f0_e0_name"]["text"] == "plan.md"
    assert shown["up"]["action"]["event"]["context"] == {"target": str(home), "kind": "directory"}
    assert model.requests == []
    assert rows(audit, "model_calls") == []
    (row,) = rows(audit, "tool_calls")
    assert (row["tool"], row["asked_by"], row["status"]) == ("list_directory", "user", "ok")


def test_the_top_folder_offers_no_way_up(client, home):
    response = client.post(
        "/action", json=action("open", {"target": str(home), "kind": "directory"}), headers=SESSION
    )

    assert "up" not in workspace(response)


def test_opening_a_file_shows_its_text(client, model, home):
    response = client.post(
        "/action",
        json=action("open", {"target": str(home / "app.py"), "kind": "file"}),
        headers=SESSION,
    )

    shown = workspace(response)
    assert shown["title"]["text"] == "app.py"
    assert shown["f0_text"]["text"] == "```py\nprint('hi')\n\n```"
    assert model.requests == []


def test_opening_an_image_shows_it_inline(client, model, audit, home):
    (home / "pixel.png").write_bytes(base64.b64decode(PIXEL_PNG))

    response = client.post(
        "/action",
        json=action("open", {"target": str(home / "pixel.png"), "kind": "file"}),
        headers=SESSION,
    )

    shown = workspace(response)
    assert shown["f0_image"] == {
        "id": "f0_image",
        "component": "FileImage",
        "src": f"data:image/png;base64,{PIXEL_PNG}",
        "description": "pixel.png",
    }
    assert model.requests == []
    (row,) = rows(audit, "tool_calls")
    assert (row["tool"], row["asked_by"], row["status"]) == ("read_image", "user", "ok")

    ask(client, "what is this?")
    assert model.prompt().endswith(
        "(On the user's screen right now: the image pixel.png, which you cannot see.)"
    )


def test_a_file_named_like_an_image_that_is_not_one_is_not_shown(client, home):
    (home / "fake.png").write_bytes(b"<svg><script>alert(1)</script></svg>")

    response = client.post(
        "/action",
        json=action("open", {"target": str(home / "fake.png"), "kind": "file"}),
        headers=SESSION,
    )

    (event,) = events(response)
    assert event["type"] == "error" and "not an image" in event["message"]


def test_opening_something_off_limits_is_an_error_event(client, audit, home):
    response = client.post(
        "/action",
        json=action("open", {"target": str(home / ".env"), "kind": "file"}),
        headers=SESSION,
    )

    (event,) = events(response)
    assert event["type"] == "error" and "hidden" in event["message"]
    assert rows(audit, "tool_calls")[0]["status"] == "error"


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"target": "x"},
        {"target": "x", "kind": "symlink"},
        {"target": 3},
        {"path": "x", "kind": "file"},
    ],
)
def test_a_malformed_open_action_is_rejected(client, audit, context):
    response = client.post("/action", json=action("open", context), headers=SESSION)

    assert response.status_code == 400
    assert rows(audit, "tool_calls") == []


# --- what the user is looking at ---------------------------------------------


def test_the_model_is_told_which_file_the_user_opened(client, model, home):
    client.post(
        "/action",
        json=action("open", {"target": str(home / "docs" / "plan.md"), "kind": "file"}),
        headers=SESSION,
    )

    ask(client, "summarise this")

    assert model.prompt(0) == (
        "summarise this\n\n(On the user's screen right now: the file docs/plan.md.)"
    )


def test_the_model_is_told_which_folder_a_reply_showed(client, model):
    model.script = [{"step": {"do": "list_directory", "path": "docs"}}]
    ask(client, "open docs")

    ask(client, "what is the newest one?")

    assert model.prompt().endswith("(On the user's screen right now: the folder docs.)")


def test_after_a_reply_without_files_nothing_is_said_to_be_on_screen(client, model, home):
    client.post(
        "/action",
        json=action("open", {"target": str(home / "app.py"), "kind": "file"}),
        headers=SESSION,
    )
    ask(client, "what is a symlink?")

    ask(client, "and a hard link?")

    assert model.prompt() == "and a hard link?"
    assert model.prompt(0).startswith("what is a symlink?\n\n(On the user's screen")


# --- the conversation ------------------------------------------------------


def test_each_turn_is_added_to_the_trail(client, model):
    ask(client, "first question")
    response = ask(client, "second question")

    shown = trail(response)
    assert shown["root"]["children"] == ["trail_head", "t1", "t2"]
    assert shown["t1_label"]["text"] == "first question"
    assert shown["t2_label"]["text"] == "▸ second question"
    assert shown["t2_title"]["text"] == SAMPLE_REPLY.title
    assert shown["t1"]["action"]["event"] == {"name": "revisit", "context": {"turn": 1}}


def test_the_trail_names_a_form_by_its_purpose_and_cuts_long_requests(client):
    client.post(
        "/action",
        json=action("submit", {"purpose": "Gather project details", "values": {}}),
        headers=SESSION,
    )
    response = ask(client, "x" * 200)

    shown = trail(response)
    assert shown["t1_label"]["text"] == "Form: Gather project details"
    assert len(shown["t2_label"]["text"]) == len("▸ ") + 80
    assert shown["t2_label"]["text"].endswith("…")


def test_revisiting_a_turn_shows_its_screen_again_without_a_model(client, model, audit):
    model.script = [{"step": {"do": "list_directory", "path": "docs"}}]
    ask(client, "open docs")
    ask(client, "something else")
    calls = len(model.requests), len(rows(audit, "tool_calls"))

    response = client.post("/action", json=action("revisit", {"turn": 1}), headers=SESSION)

    assert modes(response) == ["file"]
    assert workspace(response)["f0_e0_name"]["text"] == "plan.md"
    assert trail(response)["t1_label"]["text"] == "▸ open docs"
    assert (len(model.requests), len(rows(audit, "tool_calls"))) == calls


def test_a_revisited_file_is_what_the_model_is_told_is_on_screen(client, model):
    model.script = [{"step": {"do": "read_text_file", "path": "app.py"}}]
    ask(client, "show app.py")
    ask(client, "what is a symlink?")
    client.post("/action", json=action("revisit", {"turn": 1}), headers=SESSION)

    ask(client, "explain it")

    assert model.prompt().endswith("(On the user's screen right now: the file app.py.)")


@pytest.mark.parametrize("context", [{}, {"turn": "1"}, {"turn": 0}, {"turn": True}])
def test_a_malformed_revisit_is_rejected(client, context):
    response = client.post("/action", json=action("revisit", context), headers=SESSION)

    assert response.status_code == 400


def test_revisiting_a_turn_that_does_not_exist_is_an_error_event(client):
    response = client.post("/action", json=action("revisit", {"turn": 7}), headers=SESSION)

    (event,) = events(response)
    assert event["type"] == "error"


def test_a_conversation_outlives_the_agent(make_client, model):
    with make_client() as first:
        ask(first, "first question")

    with make_client() as second:
        resumed = second.post("/resume", headers=SESSION)
        ask(second, "second question")

    assert modes(resumed) == ["conversation"]
    assert workspace(resumed)["title"]["text"] == SAMPLE_REPLY.title
    assert trail(resumed)["t1_label"]["text"] == "▸ first question"
    # The restarted agent sent the model the earlier turn.
    assert model.requests[1][0].parts[-1].content == "first question"


def test_resuming_a_new_conversation_shows_nothing(client):
    response = client.post("/resume", headers={"X-Praxis-Session": "brand-new-one"})

    assert response.status_code == 200
    assert response.text == ""


def test_conversations_are_kept_apart(client, model):
    ask(client, "first")
    ask(client, "second")
    ask(client, "other", session={"X-Praxis-Session": "another-one"})

    same_session, other_session = model.requests[1], model.requests[2]
    assert len(same_session) == 3  # first request, its response, second request
    assert len(other_session) == 1


# --- other actions, sessions, origins ---------------------------------------


def test_card_action_runs_its_intent(client, model, audit):
    response = client.post(
        "/action", json=action("intent", {"text": "Show me an example"}), headers=SESSION
    )

    assert response.status_code == 200
    assert model.prompt() == "Show me an example"
    assert rows(audit, "model_calls")[0]["purpose"] == "action:intent"


def test_form_submission_reaches_the_model_with_its_values(client, model):
    context = {"purpose": "Gather project details", "values": {"project_name": "Praxis"}}

    client.post("/action", json=action("submit", context), headers=SESSION)

    assert "Gather project details" in model.prompt()
    assert '"project_name": "Praxis"' in model.prompt()


def test_unknown_action_is_rejected_without_a_model_call(client, model):
    response = client.post("/action", json=action("rm_rf", {}), headers=SESSION)

    assert response.status_code == 400
    assert model.requests == []


def test_session_header_is_required(client, model):
    response = client.post("/intent", json={"intent": "hi"})

    assert response.status_code == 422
    assert client.post("/resume").status_code == 422
    assert model.requests == []


def test_other_origins_fail_cors_preflight(client):
    def preflight(origin, path="/intent"):
        return client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-praxis-session",
            },
        )

    assert preflight("http://localhost:1420").status_code == 200
    assert preflight("https://evil.example").status_code == 403
    assert preflight("https://evil.example", "/resume").status_code == 403


def test_a_page_that_rebinds_its_domain_to_this_machine_reads_nothing(client, home, audit):
    # After DNS rebinding the page's requests are same-origin to the browser, so no
    # CORS preflight happens; only the host and origin they carry give them away.
    opened = action("open", {"target": str(home / "app.py"), "kind": "file"})
    rebound = {"Host": "attacker.example:7729", "Origin": "http://attacker.example:7729"}

    by_host = client.post("/action", json=opened, headers={**SESSION, "Host": rebound["Host"]})
    by_origin = client.post(
        "/action", json=opened, headers={**SESSION, "Origin": rebound["Origin"]}
    )

    assert by_host.status_code == 421
    assert by_origin.status_code == 403
    assert "print('hi')" not in by_host.text + by_origin.text
    assert rows(audit, "tool_calls") == []


@pytest.mark.parametrize("host", ["127.0.0.1:7729", "localhost:7729", "127.0.0.1"])
def test_the_agent_answers_on_this_machine_by_either_name(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


def test_requests_too_long_for_a_model_are_refused_whatever_carries_them(client, model):
    too_long = "x" * 8001

    typed = client.post("/intent", json={"intent": too_long}, headers=SESSION)
    clicked = client.post("/action", json=action("intent", {"text": too_long}), headers=SESSION)
    submitted = client.post(
        "/action",
        json=action("submit", {"purpose": "p", "values": {"a": too_long}}),
        headers=SESSION,
    )

    assert (typed.status_code, clicked.status_code, submitted.status_code) == (422, 413, 413)
    assert model.requests == []


# --- the kill switch and forgetting ------------------------------------------------


def test_the_kill_switch_ends_a_turn_in_progress(client, model, audit, memory):
    model.stall = True
    outcome = {}

    def ask_in_background():
        try:
            outcome["response"] = ask(client, "write me a long essay")
        except Exception as error:  # the stream may be cut mid-response
            outcome["error"] = error

    asking = threading.Thread(target=ask_in_background)
    asking.start()
    deadline = time.monotonic() + 5
    while not model.requests and time.monotonic() < deadline:
        time.sleep(0.01)

    stopped = client.post("/stop", headers=SESSION)
    asking.join(timeout=5)

    assert stopped.json() == {"stopped": 1}
    assert not asking.is_alive()
    (row,) = rows(audit, "model_calls")
    assert row["status"] == "cancelled"
    assert memory.turns("test-session") == []
    # The agent keeps working afterwards.
    model.stall = False
    assert workspace(ask(client, "hello again"))["title"]["text"] == SAMPLE_REPLY.title


def test_the_kill_switch_with_nothing_running_stops_nothing(client):
    assert client.post("/stop", headers=SESSION).json() == {"stopped": 0}


def test_forgetting_a_conversation_deletes_it_and_only_it(client, model, memory, audit):
    ask(client, "first")
    ask(client, "second")
    ask(client, "other", session={"X-Praxis-Session": "another-one"})

    forgotten = client.post("/forget", headers=SESSION)

    assert forgotten.json() == {"forgotten": 2}
    assert memory.turns("test-session") == []
    assert client.post("/resume", headers=SESSION).text == ""
    assert len(memory.turns("another-one")) == 1
    # The audit log keeps its records of what happened.
    assert len(rows(audit, "model_calls")) == 3
    # The next turn starts a fresh history.
    ask(client, "third")
    assert len(model.requests[-1]) == 1


def test_forgotten_text_is_gone_from_the_file(client, model, memory):
    ask(client, "my secret project is called NIGHTJAR")

    client.post("/forget", headers=SESSION)

    assert b"NIGHTJAR" not in memory.path.read_bytes()


# --- when the file service fails ----------------------------------------------


@pytest.fixture
def broken_files_client(audit, memory, model):
    class Broken:
        roots = ["/home/ada"]

        async def run(self, lookup):
            raise RuntimeError("praxis-fs exited")

        def parent(self, directory):
            return None

    @asynccontextmanager
    async def broken():
        yield Broken()

    app = create_app(
        audit=audit, conversations=memory, model_for=model.for_alias, connect_files=broken
    )
    with TestClient(app, base_url=AGENT_URL) as test_client:
        yield test_client


def test_a_click_when_the_file_service_is_down_says_so(broken_files_client, audit):
    response = broken_files_client.post(
        "/action", json=action("open", {"target": "docs", "kind": "directory"}), headers=SESSION
    )

    (event,) = events(response)
    assert event == {
        "type": "error",
        "message": "The file service did not answer, so nothing was read.",
    }
    (row,) = rows(audit, "tool_calls")
    assert row["status"] == "error" and "praxis-fs exited" in row["error"]


def test_a_lookup_when_the_file_service_is_down_is_told_to_the_model(
    broken_files_client, model, audit
):
    model.script = [{"step": {"do": "list_directory"}}]

    response = ask(broken_files_client, "my files")

    assert "list_directory failed: The file service did not answer" in model.prompt()
    assert workspace(response)["title"]["text"] == SAMPLE_REPLY.title
    assert rows(audit, "model_calls")[0]["status"] == "ok"


# --- what the model and the user are told ----------------------------------------


def test_a_missing_model_is_explained(client, model):
    from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

    from praxis_agent.server import model_failure

    assert "was not found" in model_failure(ModelHTTPError(404, "local-fast"), "local-fast")
    assert "refused the API key" in model_failure(ModelHTTPError(401, "x"), "cloud-strong")
    assert "LiteLLM proxy" in model_failure(ModelAPIError("x", "Connection error."), "local-fast")


def test_refusals_reach_the_model_without_the_users_folder_paths(client, model, home):
    model.script = [{"step": {"do": "read_text_file", "path": "../outside.txt"}}]

    ask(client, "read the file next to my home")

    assert "read_text_file was refused:" in model.prompt()
    assert str(home) not in model.prompt()
    assert "the main folder" in model.prompt()


def test_the_audit_log_counts_the_request_as_the_model_received_it(client, model, home, audit):
    client.post(
        "/action",
        json=action("open", {"target": str(home / "app.py"), "kind": "file"}),
        headers=SESSION,
    )

    ask(client, "summarise this")

    sent = model.prompt(0)
    assert sent != "summarise this"
    assert rows(audit, "model_calls")[0]["prompt_chars"] == len(sent)


# --- streaming, with the limits of a screen --------------------------------------


def form(*names):
    return {
        "kind": "form",
        "purpose": "p",
        "fields": [{"name": name, "label": name} for name in names],
    }


def test_a_second_form_does_not_wipe_what_was_typed_into_the_first(client, model):
    model.script = [reply(form("a"), form("b"), text("Fill both in."), mode="conversation")]

    response = ask(client, "two forms")

    paths = [m["updateDataModel"]["path"] for m in a2ui(response) if "updateDataModel" in m]
    assert paths == ["/b0", "/b1"]
    assert surfaces(response)["workspace"]["data"] == {"b0": {"a": ""}, "b1": {"b": ""}}


def test_what_was_looked_up_survives_a_reply_that_fills_the_screen(client, model):
    model.script = [
        {"step": {"do": "list_directory"}},
        {"step": {"do": "read_text_file", "path": ".env"}},
        reply(*[text(f"paragraph {i}") for i in range(20)]),
    ]

    shown = workspace(ask(client, "tell me everything"))

    children = shown["root"]["children"]
    assert len(children) == 1 + 12  # the title, and a full screen
    assert "f0" in children
    assert any(
        shown[c].get("text", "").startswith("Not read:") for c in children if c.startswith("b")
    )


# --- the real server process ------------------------------------------------


def test_praxis_fs_is_started_and_reached_over_stdio(audit, memory, model, home, monkeypatch):
    monkeypatch.setenv("PRAXIS_FS_ROOTS", str(home))
    # The default: praxis-fs as a subprocess.
    app = create_app(audit=audit, conversations=memory, model_for=model.for_alias)

    with TestClient(app, base_url=AGENT_URL) as stdio_client:
        response = stdio_client.post(
            "/action", json=action("open", {"target": "docs", "kind": "directory"}), headers=SESSION
        )

    assert workspace(response)["f0_e0_name"]["text"] == "plan.md"
