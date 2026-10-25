"""HTTP surface of the agent. The Canvas is the only intended client.

Every endpoint but /health answers with newline-delimited JSON, one event per line:

    {"type": "call",   "model": "local-fast", "destination": "local"}
    {"type": "status", "text": "Searching for *.py…"}
    {"type": "mode",   "mode": "conversation"}
    {"type": "a2ui",   "message": {...}}        # an A2UI v0.9 server-to-client message
    {"type": "error",  "message": "..."}
"""

import asyncio
import difflib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.models import Model

from . import planner, router
from .audit import AuditLog
from .memory import Conversations
from .planner import Draft, Looking, Proposing, Turn
from .tools.files import (
    IMAGE_SUFFIXES,
    MODEL_FILE_CHARS,
    FileRequest,
    FileText,
    FileTools,
    ImageFile,
    ListDirectory,
    Listing,
    Lookup,
    OpenForEditing,
    ReadImage,
    ReadTextFile,
    RecentFiles,
    SearchFiles,
    ToolFailure,
    Undone,
    UndoWrite,
    WriteFile,
    Written,
    praxis_fs,
    relative,
)
from .tools.run import RunFile, RunResult, RunTools, praxis_run
from .ui import Screen, build_trail, compile_screen, replace, update
from .ui.a2ui import WORKSPACE_SURFACE
from .ui.plan import Changed, Editor, Proposal, Ran, TextBlock

log = logging.getLogger("praxis_agent")

# Vite dev server, and the Tauri webview origin on Linux/macOS and on Windows.
ALLOWED_ORIGINS = ["http://localhost:1420", "tauri://localhost", "http://tauri.localhost"]
# The names the agent may be reached by. Anything else is a page that has rebound
# its own domain to this machine.
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

# The longest request, typed or carried by an action, that is passed to a model.
MAX_REQUEST_CHARS = 8000
# The most text a Save may carry. praxis-fs has its own, similar limit.
MAX_SAVE_CHARS = 1_000_000
# Proposed changes kept waiting per conversation; older ones expire.
MAX_PENDING = 20

# Actions that act on a file the user chose, without a model.
FILE_ACTIONS = {"edit", "save", "run", "apply", "discard", "undo"}


class IntentRequest(BaseModel):
    intent: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)


class Action(BaseModel):
    name: str
    surfaceId: str
    sourceComponentId: str
    timestamp: str
    context: dict[str, Any] = {}


class ActionMessage(BaseModel):
    """An A2UI v0.9 client-to-server action message."""

    version: str
    action: Action


def prompt_for_action(action: Action) -> tuple[str, str]:
    """The request a UI action stands for, in the user's voice, and how the trail shows it."""
    if action.name == "intent":
        text = action.context.get("text")
        if isinstance(text, str) and text.strip():
            return _bounded(text), text
    elif action.name == "submit":
        purpose = str(action.context.get("purpose", ""))
        values = json.dumps(action.context.get("values", {}), ensure_ascii=False)
        prompt = f"I filled in the form ({purpose}). My answers: {values}"
        return _bounded(prompt), f"Form: {purpose}"
    raise HTTPException(status_code=400, detail=f"Unsupported action: {action.name}")


def _bounded(prompt: str) -> str:
    if len(prompt) > MAX_REQUEST_CHARS:
        raise HTTPException(status_code=413, detail="That request is too long.")
    return prompt


def model_failure(error: Exception, alias: str) -> str:
    """What to tell the user when the model could not be used."""
    if isinstance(error, ModelHTTPError):
        if error.status_code == 404:
            return (
                f"The model behind “{alias}” was not found. "
                "Is it pulled in Ollama, and named as in litellm_config.yaml?"
            )
        if error.status_code in (401, 403):
            return f"The provider behind “{alias}” refused the API key."
        return f"The model service answered with an error (HTTP {error.status_code})."
    if isinstance(error, ModelAPIError):
        return "Could not reach the model service. Is the LiteLLM proxy running?"
    return f"The model call failed: {type(error).__name__}"


def lookup_for_open(action: Action) -> ListDirectory | ReadTextFile | ReadImage:
    """The lookup behind a click on a file or folder."""
    path, kind = action.context.get("target"), action.context.get("kind")
    if isinstance(path, str) and path:
        if kind == "directory":
            return ListDirectory(do="list_directory", path=path)
        if kind == "file" and Path(path).suffix.lower() in IMAGE_SUFFIXES:
            return ReadImage(do="read_image", path=path)
        if kind == "file":
            return ReadTextFile(do="read_text_file", path=path)
    raise HTTPException(status_code=400, detail="An open action needs a target and a kind.")


def context_text(action: Action, key: str, *, limit: int = 4096, empty: bool = False) -> str:
    """A text value from an action's context, or a 400 if it is missing or too long."""
    value = action.context.get(key)
    if isinstance(value, str) and (value or empty) and len(value) <= limit:
        return value
    raise HTTPException(status_code=400, detail=f"A {action.name} action needs {key}.")


def file_action_args(action: Action) -> dict[str, str]:
    """What a file action carries, checked before anything is streamed back."""
    if action.name == "save":
        return {
            "target": context_text(action, "target"),
            "base": context_text(action, "base", limit=64),
            "content": context_text(action, "content", limit=MAX_SAVE_CHARS, empty=True),
        }
    key = {"apply": "proposal", "discard": "proposal", "undo": "backup"}.get(action.name, "target")
    return {key: context_text(action, key)}


@dataclass
class PendingChange:
    """A change the model proposed, waiting for the user's Apply."""

    path: str
    # The hash the file had when the change was proposed; None for a new file.
    base: str | None
    content: str


# Characters that change what text means without showing: controls, line and paragraph
# separators other than "\n", zero-width and bidirectional marks (Trojan Source).
_INVISIBLE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f­؜᠎​-‏ -‮⁠-⁯﻿￹-￻]")
_TRAILING = re.compile(r"[ \t]+$")


def unified_diff(old: str, new: str, name: str) -> tuple[str, int]:
    """The change from `old` to `new` as the user is shown it, and how many invisible
    characters the new lines hold.

    Lines are split at "\\n" only, as interpreters split them, so a change hidden in
    another kind of line break shows. Invisible characters are written out as
    ⟨U+XXXX⟩, and trailing spaces and tabs as · and →.
    """
    lines = difflib.unified_diff(
        old.replace("\r\n", "\n").split("\n"),
        new.replace("\r\n", "\n").split("\n"),
        fromfile=f"a/{name}",
        tofile=f"b/{name}",
        lineterm="",
    )
    shown: list[str] = []
    invisible = 0
    for i, line in enumerate(lines):
        if i < 2 or line.startswith("@@"):
            shown.append(line)
            continue
        text, count = _INVISIBLE.subn(lambda m: f"⟨U+{ord(m.group()):04X}⟩", line[1:])
        text = _TRAILING.sub(lambda m: m.group().replace(" ", "·").replace("\t", "→"), text)
        if line[0] == "+":
            invisible += count
        shown.append(line[0] + text)
    return "\n".join(shown), invisible


def turn_to_revisit(action: Action) -> int:
    turn = action.context.get("turn")
    if isinstance(turn, int) and not isinstance(turn, bool) and turn > 0:
        return turn
    raise HTTPException(status_code=400, detail="A revisit action needs a turn number.")


def status_for(lookup: Lookup) -> str:
    """What the Canvas says while a lookup runs."""
    where = ""
    if isinstance(lookup, SearchFiles | RecentFiles) and lookup.directory:
        where = f" in {lookup.directory}"
    if isinstance(lookup, SearchFiles):
        return f"Searching for {lookup.pattern}{where}…"
    if isinstance(lookup, RecentFiles):
        days = "today" if lookup.days <= 1 else f"in the last {lookup.days} days"
        return f"Looking for files changed {days}{where}…"
    if isinstance(lookup, ListDirectory):
        return f"Opening {lookup.path or 'your main folder'}…"
    return f"Reading {lookup.path}…"


def on_screen(screen: Screen, main_folder: str) -> str | None:
    """The file or folder `screen` shows, in the words the model is given.

    The last one, if it shows several. Search results do not count: they are not one place.
    """
    for block in reversed(screen.blocks):
        if isinstance(block, FileText):
            return f"the file {relative(block.path, main_folder)}"
        if isinstance(block, Editor):
            return f"the file {relative(block.path, main_folder)}, open in the editor"
        if isinstance(block, Proposal):
            where = relative(block.path, main_folder)
            return f"a proposed change to {where}, which the user has not applied"
        if isinstance(block, Ran):
            return _run_for_model(block.result, main_folder)
        if isinstance(block, ImageFile):
            return f"the image {relative(block.path, main_folder)}, which you cannot see"
        if isinstance(block, Listing) and block.pattern is None:
            return f"the folder {relative(block.directory, main_folder)}"
    return None


def create_app(
    *,
    audit: AuditLog | None = None,
    conversations: Conversations | None = None,
    model_for: Callable[[str], Model] = planner.litellm_model,
    connect_files: Callable[[], AbstractAsyncContextManager[FileTools]] = praxis_fs,
    connect_runner: Callable[[], AbstractAsyncContextManager[RunTools]] = praxis_run,
    token: str | None = None,
) -> FastAPI:
    """The agent's HTTP app.

    `token`, or else PRAXIS_AGENT_TOKEN, is the secret the Canvas that started the
    agent was given; when there is one, every request but /health must carry it.
    """
    secret = token if token is not None else os.environ.get("PRAXIS_AGENT_TOKEN") or None
    audit_log = audit or AuditLog()
    memory = conversations or Conversations()
    tools: dict[str, FileTools] = {}
    runners: dict[str, RunTools] = {}
    # Changes the model proposed, per conversation, by id.
    proposals: dict[str, dict[str, PendingChange]] = {}
    # Which file each write this agent made changed, by backup id: so the audit log can
    # say which file an undo puts back, before praxis-fs forgets the backup.
    written_paths: dict[str, str] = {}
    # What each session's user is looking at: the file or folder on screen, if any.
    viewing: dict[str, str | None] = {}
    # Turns being worked on right now, so the kill switch can stop every one of them.
    running: set[asyncio.Task[Any]] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            tools["files"] = await stack.enter_async_context(connect_files())
            try:
                runners["run"] = await stack.enter_async_context(connect_runner())
            except Exception:
                # Everything else works without it; there is just no Run button.
                log.exception("praxis-run could not be started")
                runners["run"] = RunTools.unavailable("The code runner could not be started.")
            if not runners["run"].status.available:
                log.info("files cannot be run here: %s", runners["run"].status.reason)
            yield

    app = FastAPI(title="Praxis agent", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Praxis-Session", "X-Praxis-Token"],
    )

    # Added last, so it runs first. CORS alone does not stop a web page that has
    # pointed its own domain at 127.0.0.1 (DNS rebinding): to the browser its
    # requests are then same-origin. Such a request still names the page's host
    # and origin, which are refused here.
    @app.middleware("http")
    async def only_from_this_machine(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        host = request.headers.get("host", "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            return PlainTextResponse("This agent only answers on 127.0.0.1.", status_code=421)
        origin = request.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return PlainTextResponse("This agent only answers the Praxis Canvas.", status_code=403)
        # Any program on this machine, run by any user, can reach 127.0.0.1. With a
        # token, only the Canvas that started the agent knows it. A CORS preflight
        # carries no custom headers, and is answered without it.
        if secret and request.method != "OPTIONS" and request.url.path != "/health":
            given = request.headers.get("x-praxis-token", "")
            if not hmac.compare_digest(given.encode(), secret.encode()):
                return PlainTextResponse(
                    "This agent only answers the Canvas that started it.", status_code=401
                )
        return await call_next(request)

    # Required on every turn. A custom header also makes the request non-simple,
    # so a web page on another origin cannot send it without passing CORS.
    def session(x_praxis_session: Annotated[str, Header(min_length=8, max_length=64)]) -> str:
        return x_praxis_session

    def main_folder() -> str:
        return tools["files"].roots[0]

    def can_run() -> list[str]:
        return runners["run"].can_run()

    async def use(
        session_id: str, request: FileRequest | RunFile, asked_by: str
    ) -> Listing | FileText | ImageFile | Written | Undone | RunResult:
        """Make a tool call, recorded in the audit log before it happens."""
        tool, arguments = request.call()
        # What a failure leaves undone, in the words the user is given.
        failed = getattr(request, "failed", "nothing was read")
        service = "code runner" if isinstance(request, RunFile) else "file service"
        audited = request.audited() if isinstance(request, WriteFile | UndoWrite) else arguments
        try:
            call_id = audit_log.begin_tool(
                session_id=session_id, tool=tool, arguments=audited, asked_by=asked_by
            )
        except Exception as error:
            # No audit row, no file access.
            log.exception("audit log unavailable; refusing tool call")
            raise ToolFailure(f"The audit log is unavailable, so {failed}.") from error
        try:
            if isinstance(request, RunFile):
                result: Any = await runners["run"].run(request)
            else:
                result = await tools["files"].run(request)
        except ToolFailure as failure:
            audit_log.finish_tool(call_id, error=str(failure)[:500])
            raise
        except Exception as error:
            log.exception("%s failed", service)
            audit_log.finish_tool(call_id, error=repr(error)[:500])
            raise ToolFailure(f"The {service} did not answer, so {failed}.") from error
        except BaseException:
            # The Canvas went away mid-turn, or the agent is shutting down.
            audit_log.finish_tool(call_id, status="cancelled")
            raise
        audit_log.finish_tool(call_id)
        return result

    async def stoppable(lines: AsyncIterator[str]) -> AsyncIterator[str]:
        """`lines`, which the kill switch (POST /stop) can end at any point."""
        task = asyncio.current_task()
        if task is not None:
            running.add(task)
        try:
            async for line in lines:
                yield line
        finally:
            running.discard(task)  # type: ignore[arg-type]

    def turn(session_id: str, prompt: str, label: str, purpose: str) -> AsyncIterator[str]:
        """A model turn, which the kill switch can end at any point."""
        return stoppable(model_turn(session_id, prompt, label, purpose))

    async def model_turn(
        session_id: str, prompt: str, label: str, purpose: str
    ) -> AsyncIterator[str]:
        looking_at = viewing.get(session_id)
        if looking_at:
            prompt = f"{prompt}\n\n(On the user's screen right now: {looking_at}.)"
        route = router.route(prompt)
        try:
            call_id = audit_log.begin(
                session_id=session_id,
                model=route.model,
                destination=route.destination,
                purpose=purpose,
                prompt_chars=len(prompt),
            )
        except Exception:
            # No audit row, no model call.
            log.exception("audit log unavailable; refusing model call")
            yield _line(type="error", message="Audit log unavailable, so no model was called.")
            return

        yield _line(type="call", model=route.model, destination=route.destination)
        canvas = _Workspace()
        started = time.monotonic()
        result: Turn | None = None
        try:
            async for event in planner.plan(
                prompt,
                model=model_for(route.model),
                history=memory.history(session_id),
                lookup=lambda lookup: use(session_id, lookup, "model"),
                main_folder=main_folder(),
            ):
                if isinstance(event, Looking):
                    yield _line(type="status", text=status_for(event.lookup))
                elif isinstance(event, Proposing):
                    yield _line(type="status", text=f"Writing a change to {event.path}…")
                elif isinstance(event, Draft):
                    for line in canvas.draw(event.screen):
                        yield line
                else:
                    result = event
        except Exception as exc:
            log.exception("planning failed")
            audit_log.finish(
                call_id, status="error", duration_ms=_elapsed_ms(started), error=repr(exc)[:500]
            )
            yield _line(type="error", message=model_failure(exc, route.model))
            return
        except BaseException:
            # The Canvas went away mid-turn, or the agent is shutting down.
            audit_log.finish(call_id, status="cancelled", duration_ms=_elapsed_ms(started))
            raise
        assert result is not None
        audit_log.finish(
            call_id, status="ok", duration_ms=_elapsed_ms(started), requests=result.requests
        )

        screen = result.screen()
        if result.proposal is not None:
            screen = await propose(session_id, result)
        screen.can_run = can_run()
        viewing[session_id] = shown = on_screen(screen, main_folder())
        # Stored before it is drawn: a Canvas that goes away during the last drawing
        # still finds the turn in its trail when it comes back.
        try:
            n: int | None = memory.record(
                session_id,
                label=label,
                title=screen.title,
                mode=screen.mode,
                messages=result.messages,
                screen=replace(WORKSPACE_SURFACE, *compile_screen(screen)),
                on_screen=shown,
            )
        except Exception:
            log.exception("could not store the turn")
            n = None
        for line in canvas.draw(screen):
            yield line
        if n is None:
            yield _line(type="error", message="This reply could not be saved to the conversation.")
            return
        for line in _trail(memory, session_id, current=n):
            yield line

    async def propose(session_id: str, turn: Turn) -> Screen:
        """The screen for a change the model proposed: the change as a diff, which the
        user applies or not. Nothing is written here."""
        proposal = turn.proposal
        assert proposal is not None
        blocks: list[Any] = list(turn.reply.blocks)
        blocks += [
            TextBlock(kind="text", style="caption", text=f"Not read: {reason}")
            for reason in dict.fromkeys(turn.refused)
        ]
        screen = Screen(mode="code", title=turn.reply.title, blocks=blocks)
        # Where the file is, as praxis-fs reads the path: "~" is the home folder, and a
        # relative path starts in the main folder.
        target = Path(proposal.path).expanduser()
        if not target.is_absolute():
            target = Path(main_folder(), target)
        where = relative(str(target), main_folder())

        def not_changed(reason: str) -> Screen:
            blocks.append(TextBlock(kind="text", style="caption", text=f"Not changed: {reason}"))
            return screen

        try:
            current = await use(session_id, OpenForEditing(path=proposal.path), "model")
        except ToolFailure as failure:
            if failure.refused or not str(failure).startswith("There is no file"):
                return not_changed(str(failure))
            current = None
        if isinstance(current, FileText):
            where = relative(current.path, main_folder())
            if current.sha256 is None:
                return not_changed(f"{where} is too long, or not plain text, for Praxis to change.")
            if len(current.content) > MODEL_FILE_CHARS:
                return not_changed(
                    f"{where} is longer than Praxis can change yet: "
                    f"the model sees only its first {MODEL_FILE_CHARS:,} characters."
                )
            old = current.content
        else:
            old = ""
        new = proposal.content
        # Small models sometimes fence the file's text as if it were a reply.
        fenced = re.fullmatch(r"\s*```[^\n]*\n(.*?)\n?```\s*", new, flags=re.DOTALL)
        if fenced:
            new = fenced.group(1)
        # Models often drop the final line break; a file keeps ending as it did, and a
        # new file ends with one, like any text file.
        if new and not new.endswith("\n") and (current is None or old.endswith("\n")):
            new += "\n"
        if current is not None and old.replace("\r\n", "\n") == new.replace("\r\n", "\n"):
            return not_changed("the proposed text is the same as the file.")

        pending = proposals.setdefault(session_id, {})
        while len(pending) >= MAX_PENDING:
            pending.pop(next(iter(pending)))
        change_id = secrets.token_hex(8)
        if isinstance(current, FileText):
            path, base = current.path, current.sha256
        else:
            path, base = str(target), None
        pending[change_id] = PendingChange(path=path, base=base, content=new)
        diff, invisible = unified_diff(old, new, where)
        blocks.append(Proposal(id=change_id, path=path, diff=diff, created=current is None))
        if invisible:
            warning = (
                f"Careful: the new text holds {invisible} invisible or control "
                f"character{'s' if invisible > 1 else ''}, shown as ⟨U+…⟩. They can change "
                "what code does without showing in an editor."
            )
            blocks.append(TextBlock(kind="text", style="caption", text=warning))
        if current is None:
            screen.title = f"New file {Path(path).name}"
        return screen

    async def open_path(
        session_id: str, lookup: ListDirectory | ReadTextFile
    ) -> AsyncIterator[str]:
        """Browse by clicking: straight to the tool, no model involved."""
        try:
            result = await use(session_id, lookup, "user")
        except ToolFailure as failure:
            # use() turns every failure of the file service into a ToolFailure.
            yield _line(type="error", message=str(failure))
            return
        if isinstance(result, Listing):
            shown, up = result.directory, tools["files"].parent(result.directory)
        else:
            shown, up = result.path, None
        screen = Screen(mode="file", title=Path(shown).name or shown, blocks=[result], up=up)
        for line in show(session_id, screen):
            yield line

    def show(session_id: str, screen: Screen) -> list[str]:
        """Draw a screen no model was involved in, and note what it shows."""
        screen.can_run = can_run()
        viewing[session_id] = on_screen(screen, main_folder())
        return _Workspace().draw(screen)

    def editing(changed: Changed | None, path: str, content: str, sha256: str) -> Screen:
        blocks: list[Any] = [Editor(path=path, content=content, sha256=sha256)]
        if changed is not None:
            blocks.insert(0, changed)
        return Screen(mode="code", title=Path(path).name, blocks=blocks)

    async def on_file(session_id: str, name: str, args: dict[str, str]) -> AsyncIterator[str]:
        """Edit, save, run, apply, discard, undo: what the user's clicks on a file do.

        No model is involved. Every write is one the user asked for with this click,
        and checked against the file as they saw it.
        """
        try:
            if name == "edit":
                target = args["target"]
                opened = await use(session_id, OpenForEditing(path=target), "user")
                assert isinstance(opened, FileText)
                if opened.sha256 is None:
                    yield _line(
                        type="error",
                        message=f"Praxis can only edit a whole text file of up to 64 KB, "
                        f"and {Path(opened.path).name} is not one.",
                    )
                    return
                screen = editing(None, opened.path, opened.content, opened.sha256)
            elif name == "save":
                target, base, content = args["target"], args["base"], args["content"]
                written = await use(
                    session_id,
                    WriteFile(path=target, content=content, expected_sha256=base),
                    "user",
                )
                assert isinstance(written, Written)
                changed = Changed(path=written.path, created=written.created, backup=written.backup)
                written_paths[written.backup] = written.path
                screen = editing(changed, written.path, content, written.sha256)
            elif name == "run":
                target = args["target"]
                if not runners["run"].status.available:
                    yield _line(type="error", message=runners["run"].status.reason)
                    return
                ran = await use(session_id, RunFile(path=target), "user")
                assert isinstance(ran, RunResult)
                screen = Screen(
                    mode="code", title=f"Ran {Path(ran.path).name}", blocks=[Ran(result=ran)]
                )
            elif name == "apply":
                change = proposals.get(session_id, {}).pop(args["proposal"], None)
                if change is None:
                    yield _line(
                        type="error",
                        message="This change is no longer waiting: it was applied or discarded "
                        "already, or the agent has restarted since.",
                    )
                    return
                request = WriteFile(
                    path=change.path, content=change.content, expected_sha256=change.base
                )
                written = await use(session_id, request, "user")
                assert isinstance(written, Written)
                changed = Changed(path=written.path, created=written.created, backup=written.backup)
                written_paths[written.backup] = written.path
                screen = editing(changed, written.path, change.content, written.sha256)
            elif name == "discard":
                proposals.get(session_id, {}).pop(args["proposal"], None)
                note = TextBlock(
                    kind="text", style="caption", text="Discarded. Nothing was changed."
                )
                screen = Screen(mode="code", title="Discarded", blocks=[note])
            else:
                backup = args["backup"]
                request = UndoWrite(backup=backup, path=written_paths.get(backup))
                undone = await use(session_id, request, "user")
                assert isinstance(undone, Undone)
                where = relative(undone.path, main_folder())
                if undone.removed:
                    text = f"Undone: {where} is removed again."
                else:
                    text = f"Undone: {where} is back as it was."
                blocks: list[Any] = [TextBlock(kind="text", style="caption", text=text)]
                if not undone.removed:
                    try:
                        blocks.append(
                            await use(
                                session_id,
                                ReadTextFile(do="read_text_file", path=undone.path),
                                "user",
                            )
                        )
                    except ToolFailure:
                        pass
                screen = Screen(mode="code", title="Undone", blocks=blocks)
        except ToolFailure as failure:
            yield _line(type="error", message=str(failure))
            return
        for line in show(session_id, screen):
            yield line

    async def show_turn(session_id: str, n: int) -> AsyncIterator[str]:
        """Bring a stored turn's screen back. No model and no lookup: it is shown as it was."""
        stored = memory.screen(session_id, n)
        if stored is None:
            yield _line(type="error", message="That part of the conversation is not stored.")
            return
        viewing[session_id] = stored.on_screen
        yield _line(type="mode", mode=stored.mode)
        for message in stored.messages:
            yield _line(type="a2ui", message=message)
        for line in _trail(memory, session_id, current=n):
            yield line

    async def resume(session_id: str) -> AsyncIterator[str]:
        """Where a conversation left off: its last screen and its trail. Nothing if it is new."""
        turns = memory.turns(session_id)
        if turns:
            async for line in show_turn(session_id, turns[-1].n):
                yield line

    def stream(lines: AsyncIterator[str]) -> StreamingResponse:
        return StreamingResponse(lines, media_type="application/x-ndjson")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/intent")
    def intent(
        body: IntentRequest, session_id: Annotated[str, Depends(session)]
    ) -> StreamingResponse:
        return stream(turn(session_id, body.intent, body.intent, "intent"))

    @app.post("/action")
    def action(
        body: ActionMessage, session_id: Annotated[str, Depends(session)]
    ) -> StreamingResponse:
        if body.action.name == "open":
            return stream(open_path(session_id, lookup_for_open(body.action)))
        if body.action.name == "revisit":
            return stream(show_turn(session_id, turn_to_revisit(body.action)))
        if body.action.name in FILE_ACTIONS:
            args = file_action_args(body.action)
            return stream(stoppable(on_file(session_id, body.action.name, args)))
        prompt, label = prompt_for_action(body.action)
        return stream(turn(session_id, prompt, label, f"action:{body.action.name}"))

    @app.post("/resume")
    def resume_session(session_id: Annotated[str, Depends(session)]) -> StreamingResponse:
        return stream(resume(session_id))

    @app.post("/stop")
    def stop(session_id: Annotated[str, Depends(session)]) -> dict[str, int]:
        """The kill switch: end every turn the agent is working on, in every conversation.

        Each one's model call is cut off and recorded in the audit log as cancelled.
        """
        stopping = list(running)
        for task in stopping:
            task.cancel()
        log.warning("kill switch: stopped %d turn(s), asked by %s", len(stopping), session_id)
        return {"stopped": len(stopping)}

    @app.post("/forget")
    def forget(session_id: Annotated[str, Depends(session)]) -> dict[str, int]:
        """Delete this conversation: its turns, and what was read during them.

        The audit log keeps its records, which hold no prompt text or file contents.
        """
        viewing.pop(session_id, None)
        proposals.pop(session_id, None)
        return {"forgotten": memory.forget(session_id)}

    return app


class _Workspace:
    """Draws screens on the workspace surface during one response, as smoothly as it can.

    The first screen replaces whatever the Canvas showed. Later ones, such as a reply
    growing as it streams, are drawn in place, unless that would leave behind a
    component the new screen no longer has; then the surface is replaced again.
    """

    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.data: dict[str, Any] = {}
        self.mode: str | None = None

    def draw(self, screen: Screen) -> list[str]:
        components, data = compile_screen(screen)
        ids = {component["id"] for component in components}
        if self.ids and self.ids <= ids:
            # Only forms that are new or changed: re-sending one would wipe what
            # the user has typed into it.
            changed = {key: value for key, value in data.items() if self.data.get(key) != value}
            messages = update(WORKSPACE_SURFACE, components, changed)
        else:
            messages = replace(WORKSPACE_SURFACE, components, data)
        lines = []
        if screen.mode != self.mode:
            lines.append(_line(type="mode", mode=screen.mode))
        lines += [_line(type="a2ui", message=message) for message in messages]
        self.ids, self.data, self.mode = ids, data, screen.mode
        return lines


def _trail(memory: Conversations, session_id: str, *, current: int) -> list[str]:
    return [
        _line(type="a2ui", message=message)
        for message in build_trail(memory.turns(session_id), current=current)
    ]


def _run_for_model(result: RunResult, main_folder: str) -> str:
    """What the model is told about a run on screen: enough to explain an error."""
    where = relative(result.path, main_folder)
    if result.timed_out:
        how = "it was stopped for taking too long"
    else:
        how = f"it exited with code {result.exit_code}"
    said = []
    for name, text in (("output", result.stdout), ("errors", result.stderr)):
        if text.strip():
            said.append(f"{name}:\n{text[-1500:]}")
    return f"the result of running {where}: {how}" + ("\n" + "\n".join(said) if said else "")


def _line(**event: Any) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
