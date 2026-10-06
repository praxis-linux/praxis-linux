import json
import sqlite3
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from praxis_fs.fs import Files
from praxis_fs.server import build_server
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from praxis_agent.audit import AuditLog
from praxis_agent.server import create_app
from praxis_agent.tools.files import FileTools
from praxis_agent.ui import Screen, build_messages
from sample_plan import SAMPLE_REPLY

SESSION = {"X-Praxis-Session": "test-session"}

REPLY = {"step": SAMPLE_REPLY.model_dump()}


def reply(*blocks, mode="file", title="Found"):
    return {"step": {"do": "reply", "mode": mode, "title": title, "blocks": list(blocks)}}


def text(content):
    return {"kind": "text", "text": content, "style": "body"}


class FakeModel:
    """Stands in for the LiteLLM proxy: records prompts, answers from a script.

    Each entry of `script` is one model step; when it runs out, the model replies
    with SAMPLE_REPLY.
    """

    def __init__(self):
        self.requests = []
        self.aliases = []
        self.script = []
        self.fail = False

    def for_alias(self, alias):
        self.aliases.append(alias)
        return FunctionModel(self._respond)

    def _respond(self, messages, info):
        if self.fail:
            raise RuntimeError("proxy unreachable")
        self.requests.append(messages)
        step = self.script.pop(0) if self.script else REPLY
        return ModelResponse(parts=[TextPart(json.dumps(step))])

    def prompt(self, index=-1):
        return self.requests[index][-1].parts[-1].content


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
def client(audit, model, home):
    @asynccontextmanager
    async def in_process_praxis_fs():
        async with Client(build_server(Files([home]))) as mcp_client:
            yield await FileTools.connect(mcp_client)

    app = create_app(audit=audit, model_for=model.for_alias, connect_files=in_process_praxis_fs)
    with TestClient(app) as test_client:
        yield test_client


def events(response):
    return [json.loads(line) for line in response.text.splitlines()]


def components(response):
    (update,) = [
        e["message"]["updateComponents"]
        for e in events(response)
        if e["type"] == "a2ui" and "updateComponents" in e["message"]
    ]
    return {c["id"]: c for c in update["components"]}


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


# --- a plain turn ----------------------------------------------------------


def test_intent_streams_call_then_mode_then_a2ui(client):
    response = client.post("/intent", json={"intent": "plan a project"}, headers=SESSION)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    got = events(response)
    assert got[0] == {"type": "call", "model": "local-fast", "destination": "local"}
    assert got[1] == {"type": "mode", "mode": "conversation"}
    expected = Screen(mode="conversation", title="Plan a project", blocks=SAMPLE_REPLY.blocks)
    assert [e["message"] for e in got[2:]] == build_messages(expected)


def test_every_model_call_is_audited(client, audit):
    client.post("/intent", json={"intent": "plan a project"}, headers=SESSION)

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

    got = events(client.post("/intent", json={"intent": "hi"}, headers=SESSION))

    assert got[0] == {"type": "call", "model": "cloud-strong", "destination": "cloud"}
    assert model.aliases == ["cloud-strong"]
    assert rows(audit, "model_calls")[0]["destination"] == "cloud"


def test_no_model_call_when_the_audit_log_cannot_be_written(client, audit, model, monkeypatch):
    def broken(**_):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "begin", broken)

    got = events(client.post("/intent", json={"intent": "hi"}, headers=SESSION))

    assert [e["type"] for e in got] == ["error"]
    assert model.requests == []


def test_model_failure_is_reported_and_audited(client, audit, model):
    model.fail = True

    got = events(client.post("/intent", json={"intent": "hi"}, headers=SESSION))

    assert [e["type"] for e in got] == ["call", "error"]
    (row,) = rows(audit, "model_calls")
    assert row["status"] == "error"
    assert "proxy unreachable" in row["error"]


def test_the_model_sees_paths_relative_to_the_main_folder(client, model, home):
    model.script = [{"step": {"do": "search_files", "pattern": "*.md"}}]

    client.post("/intent", json={"intent": "my markdown files"}, headers=SESSION)

    assert "1 files matching '*.md' under the main folder:\ndocs/plan.md (" in model.prompt()
    assert str(home) not in model.prompt()
    assert str(home) not in model.requests[0][0].instructions


def test_a_screen_that_shows_files_is_in_file_mode(client, model):
    model.script = [{"step": {"do": "list_directory"}}]  # then SAMPLE_REPLY, a conversation

    got = events(client.post("/intent", json={"intent": "my files"}, headers=SESSION))

    assert got[1] == {"type": "mode", "mode": "file"}


# --- lookups the model asks for --------------------------------------------


def test_a_lookup_is_run_shown_and_fed_back_to_the_model(client, model, home):
    model.script = [
        {"step": {"do": "recent_files", "days": 7, "pattern": "*.py"}},
        reply(text("One Python file changed this week.")),
    ]

    response = client.post("/intent", json={"intent": "python files this week"}, headers=SESSION)

    # The model saw the real result before replying.
    assert "Result of recent_files:" in model.prompt()
    assert "app.py (12 bytes, modified " in model.prompt()
    # The Canvas shows the real result, not the model's account of it.
    shown = components(response)
    assert shown["root"]["children"] == ["title", "b0", "b1"]
    assert shown["b1_e0_name"]["text"] == "app.py"
    assert shown["b1_e0"]["action"]["event"]["context"]["target"] == str(home / "app.py")


def test_what_was_looked_up_goes_after_the_answer_and_before_the_cards(client, model):
    cards = {"kind": "cards", "items": [{"title": "More", "intent": "show more"}]}
    model.script = [
        {"step": {"do": "list_directory"}},
        reply(text("Here is your main folder."), cards),
    ]

    shown = components(client.post("/intent", json={"intent": "my files"}, headers=SESSION))

    assert shown["b0"]["component"] == "Text"
    assert shown["b1_head"]["text"].endswith("2 items")  # app.py and docs; .env is hidden
    assert shown["b2_c0"]["component"] == "Button"


def test_lookups_are_audited_as_asked_by_the_model(client, model, audit):
    model.script = [{"step": {"do": "read_text_file", "path": "app.py"}}]

    client.post("/intent", json={"intent": "what is in app.py"}, headers=SESSION)

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

    client.post("/intent", json={"intent": "anything new?"}, headers=SESSION)

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

    response = client.post("/intent", json={"intent": "find my .env"}, headers=SESSION)

    assert "0 files matching '.env' under the main folder." in model.prompt()
    assert "names start with a dot are usually left out" in model.prompt()
    assert not any(key.endswith("_head") for key in components(response))
    assert events(response)[1] == {"type": "mode", "mode": "conversation"}


def test_an_empty_folder_is_still_shown(client, model, home):
    (home / "empty").mkdir()
    model.script = [{"step": {"do": "list_directory", "path": "empty"}}]

    shown = components(client.post("/intent", json={"intent": "open empty"}, headers=SESSION))

    assert shown["b1_head"]["text"].endswith("0 items")


def test_a_refusal_is_stated_to_the_user_whatever_the_model_says(client, model, audit):
    model.script = [
        {"step": {"do": "read_text_file", "path": ".env"}},
        reply(text("There is no .env file."), mode="conversation"),  # what a small model says
    ]

    response = client.post("/intent", json={"intent": "show my .env"}, headers=SESSION)

    assert "read_text_file was refused:" in model.prompt() and "hidden" in model.prompt()
    assert "SECRET" not in response.text
    shown = components(response)
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

    response = client.post("/intent", json={"intent": "read my plan"}, headers=SESSION)

    assert (
        "read_text_file failed: There is no file at docs/missing.md."
        in model.requests[1][-1].parts[-1].content
    )
    assert "Not read" not in response.text
    assert components(response)["b1_e0_name"]["text"] == "docs/plan.md"


def test_the_model_must_reply_after_three_lookups(client, model):
    model.script = [{"step": {"do": "list_directory"}}] * 3

    response = client.post("/intent", json={"intent": "loop"}, headers=SESSION)

    assert len(model.requests) == 4
    assert components(response)["title"]["text"] == SAMPLE_REPLY.title


def test_no_file_is_read_when_the_audit_log_cannot_be_written(client, model, audit, monkeypatch):
    def broken(**_):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "begin_tool", broken)
    model.script = [{"step": {"do": "read_text_file", "path": "app.py"}}]

    response = client.post("/intent", json={"intent": "read app.py"}, headers=SESSION)

    assert "audit log is unavailable" in model.prompt()
    assert "print('hi')" not in response.text


# --- browsing by clicking --------------------------------------------------


def test_opening_a_folder_lists_it_without_calling_a_model(client, model, audit, home):
    response = client.post(
        "/action",
        json=action("open", {"target": str(home / "docs"), "kind": "directory"}),
        headers=SESSION,
    )

    got = events(response)
    assert got[0] == {"type": "mode", "mode": "file"}
    shown = components(response)
    assert shown["title"]["text"] == "docs"
    assert shown["b0_e0_name"]["text"] == "plan.md"
    assert shown["up"]["action"]["event"]["context"] == {"target": str(home), "kind": "directory"}
    assert model.requests == []
    assert rows(audit, "model_calls") == []
    (row,) = rows(audit, "tool_calls")
    assert (row["tool"], row["asked_by"], row["status"]) == ("list_directory", "user", "ok")


def test_the_top_folder_offers_no_way_up(client, home):
    response = client.post(
        "/action", json=action("open", {"target": str(home), "kind": "directory"}), headers=SESSION
    )

    assert "up" not in components(response)


def test_opening_a_file_shows_its_text(client, model, home):
    response = client.post(
        "/action",
        json=action("open", {"target": str(home / "app.py"), "kind": "file"}),
        headers=SESSION,
    )

    shown = components(response)
    assert shown["title"]["text"] == "app.py"
    assert shown["b0_text"]["text"] == "```py\nprint('hi')\n\n```"
    assert model.requests == []


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
    assert model.requests == []


def test_a_session_keeps_its_conversation(client, model):
    client.post("/intent", json={"intent": "first"}, headers=SESSION)
    client.post("/intent", json={"intent": "second"}, headers=SESSION)
    client.post("/intent", json={"intent": "other"}, headers={"X-Praxis-Session": "another-one"})

    same_session, other_session = model.requests[1], model.requests[2]
    assert len(same_session) == 3  # first request, its response, second request
    assert len(other_session) == 1


def test_other_origins_fail_cors_preflight(client):
    def preflight(origin):
        return client.options(
            "/intent",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-praxis-session",
            },
        )

    assert preflight("http://localhost:1420").status_code == 200
    assert preflight("https://evil.example").status_code == 400


# --- the real server process ------------------------------------------------


def test_praxis_fs_is_started_and_reached_over_stdio(audit, model, home, monkeypatch):
    monkeypatch.setenv("PRAXIS_FS_ROOTS", str(home))
    app = create_app(audit=audit, model_for=model.for_alias)  # the default: a praxis-fs subprocess

    with TestClient(app) as stdio_client:
        response = stdio_client.post(
            "/action", json=action("open", {"target": "docs", "kind": "directory"}), headers=SESSION
        )

    assert components(response)["b0_e0_name"]["text"] == "plan.md"
