"""The agent's view of the praxis-fs MCP server: typed lookups and their results.

The server is read-only and confined to the folders the user allows; see
mcp-servers/praxis-fs. Everything here goes through MCP — the agent never opens
the user's files itself.
"""

import os
import shlex
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from mcp import Client, StdioServerParameters
from pydantic import BaseModel, Field

# How much of a result the model is shown. The Canvas shows all of it.
MODEL_ENTRIES = 30
MODEL_FILE_CHARS = 4000


class Entry(BaseModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size: int | None
    modified: datetime


class Listing(BaseModel):
    directory: str
    pattern: str | None = None
    entries: list[Entry]
    total: int
    truncated: bool


class FileText(BaseModel):
    path: str
    size: int
    content: str
    truncated: bool


# --- Lookups the model can ask for. `do` comes first so it commits to one. ---


#
# Each says which praxis-fs tool it stands for and with what arguments. A lookup has
# no optional fields: a small model fills every field it is offered, so an optional
# "changed in the last N days" on the search turned every search into a recent-files
# search. Searching and finding recent files are separate lookups instead.


class SearchFiles(BaseModel):
    """Find files by name anywhere under a folder, whenever they were last changed."""

    do: Literal["search_files"]
    pattern: str = Field(description="File name pattern, e.g. '*.py' or 'report'.")
    directory: str = Field(
        default="", description="Folder to search, e.g. 'Documents'. Empty: the main folder."
    )

    def call(self) -> tuple[str, dict[str, Any]]:
        return "search_files", {"pattern": self.pattern, "directory": self.directory}


class RecentFiles(BaseModel):
    """Find the files changed in the last few days, newest first."""

    do: Literal["recent_files"]
    days: int = Field(description="How many days back. 7 for 'this week'.")
    pattern: str = Field(default="*", description="File name pattern, e.g. '*.py'. '*': any file.")
    directory: str = Field(
        default="", description="Folder to search, e.g. 'Documents'. Empty: the main folder."
    )

    def call(self) -> tuple[str, dict[str, Any]]:
        return "search_files", {
            "pattern": self.pattern,
            "directory": self.directory,
            "modified_within_days": max(1, self.days),
        }


class ListDirectory(BaseModel):
    """List what is directly inside one folder."""

    do: Literal["list_directory"]
    path: str = Field(
        default="", description="Folder to list, e.g. 'Documents'. Empty: the main folder."
    )

    def call(self) -> tuple[str, dict[str, Any]]:
        return "list_directory", {"path": self.path}


class ReadTextFile(BaseModel):
    """Read one text file. Search for it first if you do not know its path."""

    do: Literal["read_text_file"]
    path: str = Field(description="For example 'Documents/notes.txt'.")

    def call(self) -> tuple[str, dict[str, Any]]:
        return "read_text_file", {"path": self.path}


Lookup = SearchFiles | RecentFiles | ListDirectory | ReadTextFile

# praxis-fs starts an error with this when the answer is "not allowed", not "not there".
REFUSED = "Refused: "


class ToolFailure(Exception):
    """The tool answered with an error. The message is fit to show."""

    def __init__(self, message: str) -> None:
        self.refused = message.startswith(REFUSED)
        super().__init__(message.removeprefix(REFUSED))


class FileTools:
    def __init__(self, client: Client, roots: list[str]) -> None:
        self._client = client
        self.roots = roots

    @classmethod
    async def connect(cls, client: Client) -> "FileTools":
        tools = cls(client, [])
        tools.roots = (await tools._call("allowed_folders", {}))["result"]
        return tools

    async def run(self, lookup: Lookup) -> Listing | FileText:
        tool, arguments = lookup.call()
        result = await self._call(tool, arguments)
        return (FileText if tool == "read_text_file" else Listing).model_validate(result)

    def parent(self, directory: str) -> str | None:
        """The folder above `directory`, if that is still somewhere the server may read."""
        parent = Path(directory).parent
        inside = any(parent.is_relative_to(root) for root in self.roots)
        return str(parent) if inside and str(parent) != directory else None

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._client.call_tool(tool, arguments)
        if result.is_error:
            message = result.content[0].text if result.content else f"{tool} failed."
            raise ToolFailure(message.removeprefix(f"Error executing tool {tool}: "))
        return result.structured_content or {}


@asynccontextmanager
async def praxis_fs() -> AsyncIterator[FileTools]:
    """Start the praxis-fs server as a subprocess and connect to it over stdio."""
    command = shlex.split(os.environ.get("PRAXIS_FS_COMMAND", ""), posix=os.name != "nt") or [
        sys.executable,
        "-m",
        "praxis_fs",
    ]
    # The SDK's default child environment is minimal; praxis-fs reads its roots from ours.
    server = StdioServerParameters(command=command[0], args=command[1:], env=dict(os.environ))
    async with Client(server) as client:
        yield await FileTools.connect(client)


def describe(result: Listing | FileText, main_folder: str) -> str:
    """A lookup's result as the model sees it: compact, and cut short if long.

    Paths are given relative to the main folder, which is how the model is told to
    write them, and which keeps the user's absolute paths out of the prompt.
    """

    def short(path: str) -> str:
        try:
            return Path(path).relative_to(main_folder).as_posix()
        except ValueError:
            return path

    if isinstance(result, FileText):
        shown = result.content[:MODEL_FILE_CHARS]
        cut = result.truncated or len(shown) < len(result.content)
        return f"Contents of {short(result.path)}{' (the beginning only)' if cut else ''}:\n{shown}"

    where = "the main folder" if result.directory == main_folder else short(result.directory)
    if result.pattern is None:
        head = f"{result.total} entries in {where}"
    else:
        head = f"{result.total} files matching {result.pattern!r} under {where}"
    lines = [
        f"{short(entry.path)}/"
        if entry.kind == "directory"
        else f"{short(entry.path)} ({human_size(entry.size)}, modified {entry.modified:%Y-%m-%d})"
        for entry in result.entries[:MODEL_ENTRIES]
    ]
    more = result.total - len(lines)
    if more > 0:
        lines.append(f"... and {more} more")
    if not lines:
        # So the model does not tell the user a hidden file does not exist.
        return f"{head}. Files and folders whose names start with a dot are usually left out."
    return "\n".join([f"{head}:", *lines])


def human_size(size: int | None) -> str:
    if size is None:
        return ""
    if size < 1024:
        return "1 byte" if size == 1 else f"{size} bytes"
    value = size / 1024
    for unit in ("KB", "MB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
