"""The praxis-run MCP server: run a file in a sandbox, served over stdio."""

import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from praxis_fs.fs import AccessError, Files, Refused, roots_from_env

from .sandbox import Runner, RunResult, Status

# Runs code, though in a sandbox that keeps the user's files as they are.
RUNS_CODE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)


def build_server(runner: Runner) -> MCPServer:
    server = MCPServer(
        "praxis-run",
        instructions=(
            "Runs a file the user chose, in a sandbox without network access. "
            "Run only what the user asked to run."
        ),
    )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def runner_status() -> Status:
        """Whether files can be run on this machine, and which kinds."""
        return runner.status()

    @server.tool(annotations=RUNS_CODE)
    async def run_file(path: str) -> RunResult:
        """Run a Python, shell or JavaScript file and return what it printed.

        It runs in a sandbox: no network, and it cannot change the user's files.
        It is stopped after a few seconds.
        """
        try:
            return await runner.run(path)
        except Refused as refusal:
            # The same prefix praxis-fs uses: "not allowed", as opposed to "not there".
            raise ToolError(f"Refused: {refusal}") from refusal
        except AccessError as error:
            raise ToolError(str(error)) from error

    return server


def main() -> None:
    files = Files(roots_from_env(), allow_hidden=os.environ.get("PRAXIS_FS_ALLOW_HIDDEN") == "1")
    build_server(Runner(files)).run("stdio")


if __name__ == "__main__":
    main()
