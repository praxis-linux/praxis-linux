"""The agent's view of the praxis-fs MCP server: typed requests and their results.

The server is confined to the folders the user allows; see mcp-servers/praxis-fs.
Everything here goes through MCP — the agent never opens the user's files itself.
The model may ask for lookups. Writes are only ever asked for by the user's own
click; see the server.
"""

import os
import shlex
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from mcp import Client, StdioServerParameters
from pydantic import BaseModel, Field

# A lookup that takes longer than this has failed. praxis-fs stops its own searches sooner.
TOOL_TIMEOUT_SECONDS = 30.0

# How much of a result the model is shown. The Canvas shows all of it.
MODEL_ENTRIES = 30
MODEL_FILE_CHARS = 4000
# The longest file the editor opens. praxis-fs reads at most this much.
MAX_EDIT_BYTES = 64_000


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
    # Set when the whole file was read and is UTF-8 text: needed to change it.
    sha256: str | None = None


class ImageFile(BaseModel):
    path: str
    size: int
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    # The file's bytes, base64-encoded.
    data: str


class Written(BaseModel):
    path: str
    size: int
    sha256: str
    created: bool
    # What undoes the write.
    backup: str


class Undone(BaseModel):
    path: str
    # True when the write being undone had created the file.
    removed: bool


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


class ReadImage(BaseModel):
    """Show one image. Asked for by a click, never by the model: a text model cannot see it."""

    do: Literal["read_image"]
    path: str

    def call(self) -> tuple[str, dict[str, Any]]:
        return "read_image", {"path": self.path}


Lookup = SearchFiles | RecentFiles | ListDirectory | ReadTextFile | ReadImage


# --- Requests only the user's clicks make. ---


class OpenForEditing(BaseModel):
    """Read a whole file, as long as the editor takes, to edit it."""

    path: str

    def call(self) -> tuple[str, dict[str, Any]]:
        return "read_text_file", {"path": self.path, "max_bytes": MAX_EDIT_BYTES}


class WriteFile(BaseModel):
    """Write a file the user saved or a change the user applied."""

    # What a failure leaves behind, said to the user.
    failed: ClassVar[str] = "nothing was changed"

    path: str
    content: str
    # The hash of the file being changed; None to create a new file.
    expected_sha256: str | None

    def call(self) -> tuple[str, dict[str, Any]]:
        return "write_text_file", {
            "path": self.path,
            "content": self.content,
            "expected_sha256": self.expected_sha256,
        }

    def audited(self) -> dict[str, Any]:
        """What the audit log keeps: never the text itself."""
        return {
            "path": self.path,
            "chars": len(self.content),
            "expected_sha256": self.expected_sha256,
        }


class UndoWrite(BaseModel):
    failed: ClassVar[str] = "nothing was changed"

    backup: str

    def call(self) -> tuple[str, dict[str, Any]]:
        return "undo_write", {"backup": self.backup}


FileRequest = Lookup | OpenForEditing | WriteFile | UndoWrite

# The file names a click opens as an image rather than as text. praxis-fs checks
# the bytes; this only picks which tool to ask.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

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

    async def run(self, request: FileRequest) -> Listing | FileText | ImageFile | Written | Undone:
        tool, arguments = request.call()
        result = await self._call(tool, arguments)
        kind = {
            "read_text_file": FileText,
            "read_image": ImageFile,
            "write_text_file": Written,
            "undo_write": Undone,
        }.get(tool, Listing)
        return kind.model_validate(result)

    def parent(self, directory: str) -> str | None:
        """The folder above `directory`, if that is still somewhere the server may read."""
        parent = Path(directory).parent
        inside = any(parent.is_relative_to(root) for root in self.roots)
        return str(parent) if inside and str(parent) != directory else None

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await call_tool(self._client, tool, arguments)


async def call_tool(client: Client, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool; an error it answers with becomes a ToolFailure."""
    result = await client.call_tool(tool, arguments, read_timeout_seconds=TOOL_TIMEOUT_SECONDS)
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
        # One entry per line: a line break inside a file name must not start a new one.
        return relative(path, main_folder).replace("\n", "\\n").replace("\r", "\\r")

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
        # In local time, as the Canvas shows it: "today" means the user's today.
        else f"{short(entry.path)} ({human_size(entry.size)}, "
        f"modified {entry.modified.astimezone():%Y-%m-%d})"
        for entry in result.entries[:MODEL_ENTRIES]
    ]
    more = result.total - len(lines)
    if more > 0:
        lines.append(f"... and {more} more")
    if not lines:
        # So the model does not tell the user a hidden file does not exist.
        return f"{head}. Files and folders whose names start with a dot are usually left out."
    return "\n".join([f"{head}:", *lines])


def relative(path: str, main_folder: str) -> str:
    """`path` as the model is told to write paths: relative to the main folder."""
    try:
        below = Path(path).relative_to(main_folder).as_posix()
    except ValueError:
        return path
    return "the main folder" if below == "." else below


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
