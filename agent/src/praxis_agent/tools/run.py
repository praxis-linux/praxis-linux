"""The agent's view of the praxis-run MCP server, which runs a file in a sandbox.

Only the user's click on Run asks for a run; the model cannot. See
mcp-servers/praxis-run for what the sandbox allows.
"""

import os
import shlex
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar

from mcp import Client, StdioServerParameters
from pydantic import BaseModel

from .files import call_tool


class RunnerStatus(BaseModel):
    available: bool
    reason: str
    # Suffixes of the files that can be run, such as ".py".
    suffixes: list[str]


class RunResult(BaseModel):
    path: str
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    seconds: float


class RunFile(BaseModel):
    failed: ClassVar[str] = "nothing was run"

    path: str

    def call(self) -> tuple[str, dict[str, Any]]:
        return "run_file", {"path": self.path}


class RunTools:
    def __init__(self, client: Client | None, status: RunnerStatus) -> None:
        self._client = client
        self.status = status

    @classmethod
    async def connect(cls, client: Client) -> "RunTools":
        status = RunnerStatus.model_validate(await call_tool(client, "runner_status", {}))
        return cls(client, status)

    @classmethod
    def unavailable(cls, reason: str) -> "RunTools":
        return cls(None, RunnerStatus(available=False, reason=reason, suffixes=[]))

    def can_run(self) -> list[str]:
        """The suffixes of files a Run button may be offered for."""
        return self.status.suffixes if self.status.available else []

    async def run(self, request: RunFile) -> RunResult:
        assert self._client is not None, "nothing can be run here"
        tool, arguments = request.call()
        return RunResult.model_validate(await call_tool(self._client, tool, arguments))


@asynccontextmanager
async def praxis_run() -> AsyncIterator[RunTools]:
    """Start the praxis-run server as a subprocess and connect to it over stdio."""
    command = shlex.split(os.environ.get("PRAXIS_RUN_COMMAND", ""), posix=os.name != "nt") or [
        sys.executable,
        "-m",
        "praxis_run",
    ]
    server = StdioServerParameters(command=command[0], args=command[1:], env=dict(os.environ))
    async with Client(server) as client:
        yield await RunTools.connect(client)
