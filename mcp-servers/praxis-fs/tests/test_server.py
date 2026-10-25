"""The tools as an MCP client sees them."""

import pytest
from mcp import Client

from praxis_fs.fs import Files
from praxis_fs.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def home(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    return tmp_path


@pytest.fixture
async def client(home, tmp_path_factory):
    files = Files([home], backups=tmp_path_factory.mktemp("backups"))
    async with Client(build_server(files)) as connected:
        yield connected


async def test_only_the_write_tools_are_declared_as_changing_anything(client):
    tools = {tool.name: tool.annotations for tool in (await client.list_tools()).tools}

    reads = {"allowed_folders", "list_directory", "search_files", "read_text_file", "read_image"}
    writes = {"write_text_file", "undo_write"}
    assert set(tools) == reads | writes
    assert all(tools[name].read_only_hint for name in reads)
    assert all(not tools[name].read_only_hint for name in writes)
    assert all(tools[name].destructive_hint for name in writes)


async def test_write_then_undo(client, home):
    read = await client.call_tool("read_text_file", {"path": "docs/a.txt"})
    base = read.structured_content["sha256"]

    written = await client.call_tool(
        "write_text_file", {"path": "docs/a.txt", "content": "beta", "expected_sha256": base}
    )
    assert not written.is_error
    assert (home / "docs" / "a.txt").read_text(encoding="utf-8") == "beta"

    undone = await client.call_tool("undo_write", {"backup": written.structured_content["backup"]})
    assert undone.structured_content == {"path": str(home / "docs" / "a.txt"), "removed": False}
    assert (home / "docs" / "a.txt").read_text(encoding="utf-8") == "alpha"


async def test_a_write_to_a_hidden_file_is_refused(client):
    result = await client.call_tool("write_text_file", {"path": ".bashrc", "content": "x"})

    assert result.is_error
    assert result.content[0].text.startswith("Error executing tool write_text_file: Refused: ")


async def test_listing_comes_back_as_structured_content(client, home):
    result = await client.call_tool("list_directory", {"path": str(home)})

    assert not result.is_error
    listing = result.structured_content
    assert listing["directory"] == str(home)
    assert [(e["name"], e["kind"]) for e in listing["entries"]] == [("docs", "directory")]


async def test_search_and_read(client, home):
    found = await client.call_tool("search_files", {"pattern": "*.txt", "directory": str(home)})
    (entry,) = found.structured_content["entries"]

    text = await client.call_tool("read_text_file", {"path": entry["path"]})

    assert text.structured_content["content"] == "alpha"


async def test_refusals_are_tool_errors_that_say_why(client):
    result = await client.call_tool("read_text_file", {"path": ".env"})

    assert result.is_error
    assert result.content[0].text == (
        "Error executing tool read_text_file: Refused: .env is hidden, "
        "and Praxis does not read hidden files or folders."
    )


async def test_a_missing_file_is_an_error_but_not_a_refusal(client):
    result = await client.call_tool("read_text_file", {"path": "nope.txt"})

    assert result.is_error
    assert result.content[0].text == (
        "Error executing tool read_text_file: There is no file at nope.txt."
    )


async def test_allowed_folders_reports_the_roots(client, home):
    result = await client.call_tool("allowed_folders", {})

    assert result.structured_content == {"result": [str(home)]}
