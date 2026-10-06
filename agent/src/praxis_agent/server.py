"""HTTP surface of the agent. The Canvas is the only intended client.

Both endpoints answer with newline-delimited JSON, one event per line:

    {"type": "call",  "model": "local-fast", "destination": "local"}
    {"type": "mode",  "mode": "conversation"}
    {"type": "a2ui",  "message": {...}}        # an A2UI v0.9 server-to-client message
    {"type": "error", "message": "..."}
"""

import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from . import planner, router
from .audit import AuditLog
from .tools.files import (
    FileText,
    FileTools,
    ListDirectory,
    Listing,
    Lookup,
    ReadTextFile,
    ToolFailure,
    praxis_fs,
)
from .ui import Screen, build_messages

log = logging.getLogger("praxis_agent")

# Vite dev server, and the Tauri webview origin on Linux/macOS and on Windows.
ALLOWED_ORIGINS = ["http://localhost:1420", "tauri://localhost", "http://tauri.localhost"]

# Turns of context kept per session. Memory proper lands with M0.9.
MAX_HISTORY_MESSAGES = 20


class IntentRequest(BaseModel):
    intent: str = Field(min_length=1, max_length=8000)


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


def prompt_for_action(action: Action) -> str:
    """The request a UI action stands for, in the user's voice."""
    if action.name == "intent":
        text = action.context.get("text")
        if isinstance(text, str) and text.strip():
            return text
    elif action.name == "submit":
        purpose = action.context.get("purpose", "")
        values = json.dumps(action.context.get("values", {}), ensure_ascii=False)
        return f"I filled in the form ({purpose}). My answers: {values}"
    raise HTTPException(status_code=400, detail=f"Unsupported action: {action.name}")


def lookup_for_open(action: Action) -> ListDirectory | ReadTextFile:
    """The lookup behind a click on a file or folder."""
    path, kind = action.context.get("target"), action.context.get("kind")
    if isinstance(path, str) and path:
        if kind == "directory":
            return ListDirectory(do="list_directory", path=path)
        if kind == "file":
            return ReadTextFile(do="read_text_file", path=path)
    raise HTTPException(status_code=400, detail="An open action needs a target and a kind.")


def create_app(
    *,
    audit: AuditLog | None = None,
    model_for: Callable[[str], Model] = planner.litellm_model,
    connect_files: Callable[[], AbstractAsyncContextManager[FileTools]] = praxis_fs,
) -> FastAPI:
    audit_log = audit or AuditLog()
    histories: dict[str, list[ModelMessage]] = {}
    tools: dict[str, FileTools] = {}

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

    # Required on every turn. A custom header also makes the request non-simple,
    # so a web page on another origin cannot send it without passing CORS.
    def session(x_praxis_session: Annotated[str, Header(min_length=8, max_length=64)]) -> str:
        return x_praxis_session

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
        audit_log.finish_tool(call_id)
        return result

    async def turn(session_id: str, prompt: str, purpose: str) -> AsyncIterator[str]:
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
        started = time.monotonic()
        try:
            result = await planner.plan(
                prompt,
                model=model_for(route.model),
                history=histories.get(session_id, []),
                lookup=lambda lookup: look_up(session_id, lookup, "model"),
                main_folder=tools["files"].roots[0],
            )
        except Exception as exc:
            log.exception("planning failed")
            audit_log.finish(
                call_id, status="error", duration_ms=_elapsed_ms(started), error=repr(exc)[:500]
            )
            yield _line(type="error", message=f"The model call failed: {type(exc).__name__}")
            return
        audit_log.finish(
            call_id, status="ok", duration_ms=_elapsed_ms(started), requests=result.requests
        )

        histories[session_id] = result.messages[-MAX_HISTORY_MESSAGES:]
        for line in _show(result.screen()):
            yield line

    async def open_path(
        session_id: str, lookup: ListDirectory | ReadTextFile
    ) -> AsyncIterator[str]:
        """Browse by clicking: straight to the tool, no model involved."""
        try:
            result = await look_up(session_id, lookup, "user")
        except ToolFailure as failure:
            yield _line(type="error", message=str(failure))
            return
        if isinstance(result, Listing):
            shown, up = result.directory, tools["files"].parent(result.directory)
        else:
            shown, up = result.path, None
        screen = Screen(mode="file", title=Path(shown).name or shown, blocks=[result], up=up)
        for line in _show(screen):
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
        return stream(turn(session_id, body.intent, "intent"))

    @app.post("/action")
    def action(
        body: ActionMessage, session_id: Annotated[str, Depends(session)]
    ) -> StreamingResponse:
        if body.action.name == "open":
            return stream(open_path(session_id, lookup_for_open(body.action)))
        prompt = prompt_for_action(body.action)
        return stream(turn(session_id, prompt, f"action:{body.action.name}"))

    return app


def _show(screen: Screen) -> list[str]:
    return [
        _line(type="mode", mode=screen.mode),
        *(_line(type="a2ui", message=message) for message in build_messages(screen)),
    ]


def _line(**event: Any) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
