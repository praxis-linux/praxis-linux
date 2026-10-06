"""The praxis-fs MCP server: the tools in `fs.Files`, served over stdio."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .fs import AccessError, Files, FileText, Listing, Refused, roots_from_env

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

# How a caller tells "not allowed" from "not there": the error text starts with this.
REFUSED = "Refused: "


@contextmanager
def _refusals_reach_the_client() -> Iterator[None]:
    # The SDK withholds the text of unexpected exceptions; these are answers the
    # caller needs to read, so they are raised as the error type that is passed on.
    try:
        yield
    except Refused as refusal:
        raise ToolError(f"{REFUSED}{refusal}") from refusal
    except AccessError as error:
        raise ToolError(str(error)) from error


def build_server(files: Files) -> MCPServer:
    server = MCPServer(
        "praxis-fs",
        instructions="Read-only access to the user's files. Nothing here can change a file.",
    )

    @server.tool(annotations=READ_ONLY)
    def allowed_folders() -> list[str]:
        """The folders this server may read. The first is the main one: relative and
        empty paths start there."""
        return [str(root) for root in files.roots]

    @server.tool(annotations=READ_ONLY)
    def list_directory(path: str = "") -> Listing:
        """List what is directly inside one folder. Empty means the main folder."""
        with _refusals_reach_the_client():
            return files.list_directory(path)

    @server.tool(annotations=READ_ONLY)
    def search_files(
        pattern: str = "*", directory: str = "", modified_within_days: int | None = None
    ) -> Listing:
        """Find files by name under a folder, at any depth, newest first.

        `pattern` is a file-name pattern such as '*.py'; a plain word matches any name
        containing it, and several patterns may be separated by commas.
        `modified_within_days` keeps only files changed that recently.
        """
        with _refusals_reach_the_client():
            return files.search_files(pattern, directory, modified_within_days)

    @server.tool(annotations=READ_ONLY)
    def read_text_file(path: str, max_bytes: int = 20_000) -> FileText:
        """Read a text file. Long files are cut off at `max_bytes`."""
        with _refusals_reach_the_client():
            return files.read_text_file(path, max_bytes)

    return server


def main() -> None:
    files = Files(roots_from_env(), allow_hidden=os.environ.get("PRAXIS_FS_ALLOW_HIDDEN") == "1")
    build_server(files).run("stdio")


if __name__ == "__main__":
    main()
