"""HTTP surface of the agent. The Canvas is the only intended client.

Every endpoint but /health answers with newline-delimited JSON, one event per line:

    {"type": "call",   "model": "local-fast", "destination": "local"}
    {"type": "status", "text": "Searching for *.py…"}
    {"type": "mode",   "mode": "conversation"}
    {"type": "a2ui",   "message": {...}}        # an A2UI v0.9 server-to-client message
    {"type": "error",  "message": "..."}
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
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
from .planner import Draft, Looking, Turn
from .tools.files import (
    IMAGE_SUFFIXES,
    FileText,
    FileTools,
    ImageFile,
    ListDirectory,
    Listing,
    Lookup,
    ReadImage,
    ReadTextFile,
    RecentFiles,
    SearchFiles,
    ToolFailure,
    praxis_fs,
    relative,
)
from .ui import Screen, build_trail, compile_screen, replace, update
from .ui.a2ui import WORKSPACE_SURFACE

log = logging.getLogger("praxis_agent")

# Vite dev server, and the Tauri webview origin on Linux/macOS and on Windows.
ALLOWED_ORIGINS = ["http://localhost:1420", "tauri://localhost", "http://tauri.localhost"]
# The names the agent may be reached by. Anything else is a page that has rebound
# its own domain to this machine.
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

# The longest request, typed or carried by an action, that is passed to a model.
MAX_REQUEST_CHARS = 8000


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
) -> FastAPI:
    audit_log = audit or AuditLog()
    memory = conversations or Conversations()
    tools: dict[str, FileTools] = {}
    # What each session's user is looking at: the file or folder on screen, if any.
    viewing: dict[str, str | None] = {}
    # Turns being worked on right now, so the kill switch can stop every one of them.
    running: set[asyncio.Task[Any]] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with connect_files() as files:
            tools["files"] = files
            yield

    app = FastAPI(title="Praxis agent", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Praxis-Session"],
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
        return await call_next(request)

    # Required on every turn. A custom header also makes the request non-simple,
    # so a web page on another origin cannot send it without passing CORS.
    def session(x_praxis_session: Annotated[str, Header(min_length=8, max_length=64)]) -> str:
        return x_praxis_session

    def main_folder() -> str:
        return tools["files"].roots[0]

    async def look_up(session_id: str, lookup: Lookup, asked_by: str) -> Listing | FileText:
        """Run a file lookup, recorded in the audit log before it happens."""
        tool, arguments = lookup.call()
        try:
            call_id = audit_log.begin_tool(
                session_id=session_id, tool=tool, arguments=arguments, asked_by=asked_by
            )
        except Exception as error:
            # No audit row, no file access.
            log.exception("audit log unavailable; refusing tool call")
            raise ToolFailure("The audit log is unavailable, so nothing was read.") from error
        try:
            result = await tools["files"].run(lookup)
        except ToolFailure as failure:
            audit_log.finish_tool(call_id, error=str(failure)[:500])
            raise
        except Exception as error:
            log.exception("praxis-fs failed")
            audit_log.finish_tool(call_id, error=repr(error)[:500])
            raise ToolFailure("The file service did not answer, so nothing was read.") from error
        except BaseException:
            # The Canvas went away mid-turn, or the agent is shutting down.
            audit_log.finish_tool(call_id, status="cancelled")
            raise
        audit_log.finish_tool(call_id)
        return result

    async def turn(session_id: str, prompt: str, label: str, purpose: str) -> AsyncIterator[str]:
        """A model turn, which the kill switch (POST /stop) can end at any point."""
        task = asyncio.current_task()
        if task is not None:
            running.add(task)
        try:
            async for line in model_turn(session_id, prompt, label, purpose):
                yield line
        finally:
            running.discard(task)  # type: ignore[arg-type]

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
                lookup=lambda lookup: look_up(session_id, lookup, "model"),
                main_folder=main_folder(),
            ):
                if isinstance(event, Looking):
                    yield _line(type="status", text=status_for(event.lookup))
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

    async def open_path(
        session_id: str, lookup: ListDirectory | ReadTextFile
    ) -> AsyncIterator[str]:
        """Browse by clicking: straight to the tool, no model involved."""
        try:
            result = await look_up(session_id, lookup, "user")
        except ToolFailure as failure:
            # look_up turns every failure of the file service into a ToolFailure.
            yield _line(type="error", message=str(failure))
            return
        if isinstance(result, Listing):
            shown, up = result.directory, tools["files"].parent(result.directory)
        else:
            shown, up = result.path, None
        screen = Screen(mode="file", title=Path(shown).name or shown, blocks=[result], up=up)
        viewing[session_id] = on_screen(screen, main_folder())
        for line in _Workspace().draw(screen):
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


def _line(**event: Any) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
