"""The praxis-fs MCP server: the tools in `fs.Files`, served over stdio."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .fs import (
    AccessError,
    Files,
    FileText,
    ImageFile,
    Listing,
    Refused,
    Undone,
    Written,
    backups_from_env,
    roots_from_env,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)

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
        instructions=(
            "Access to the user's files: reading, and writing text files. Write only what "
            "the user has seen and agreed to; every write can be undone with undo_write."
        ),
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

    @server.tool(annotations=READ_ONLY)
    def read_image(path: str) -> ImageFile:
        """Read a PNG, JPEG, GIF or WebP image, base64-encoded, to show it."""
        with _refusals_reach_the_client():
            return files.read_image(path)

    @server.tool(annotations=WRITES)
    def write_text_file(path: str, content: str, expected_sha256: str | None = None) -> Written:
        """Write a text file, keeping what it replaces.

        To change a file, pass the sha256 read_text_file gave for it; the write is
        refused if the file has changed since. Without it, only a new file is created.
        The result's `backup` undoes the write with undo_write.
        """
        with _refusals_reach_the_client():
            return files.write_text_file(path, content, expected_sha256)

    @server.tool(annotations=WRITES)
    def undo_write(backup: str) -> Undone:
        """Put a file back as it was before a write, unless it has changed since."""
        with _refusals_reach_the_client():
            return files.undo_write(backup)

    return server


def main() -> None:
    files = Files(
        roots_from_env(),
        allow_hidden=os.environ.get("PRAXIS_FS_ALLOW_HIDDEN") == "1",
        backups=backups_from_env(),
    )
    build_server(files).run("stdio")


if __name__ == "__main__":
    main()
