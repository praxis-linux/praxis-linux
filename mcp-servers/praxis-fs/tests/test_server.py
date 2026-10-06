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
async def client(home):
    async with Client(build_server(Files([home]))) as connected:
        yield connected


async def test_every_tool_is_declared_read_only(client):
    tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == {
        "allowed_folders",
        "list_directory",
        "search_files",
        "read_text_file",
    }
    assert all(tool.annotations.read_only_hint for tool in tools)


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
