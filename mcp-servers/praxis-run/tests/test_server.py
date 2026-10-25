"""The tools as an MCP client sees them."""

import sys

import pytest
from mcp import Client
from praxis_fs.fs import Files

from praxis_run.sandbox import Runner
from praxis_run.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    files = Files([tmp_path / "home"], backups=tmp_path / "backups")
    async with Client(build_server(Runner(files))) as connected:
        yield connected


async def test_two_tools_and_only_running_is_declared_as_changing_anything(client):
    tools = {tool.name: tool.annotations for tool in (await client.list_tools()).tools}

    assert set(tools) == {"runner_status", "run_file"}
    assert tools["runner_status"].read_only_hint
    assert not tools["run_file"].read_only_hint
    assert not tools["run_file"].open_world_hint


async def test_status_comes_back_as_structured_content(client):
    result = await client.call_tool("runner_status", {})

    assert set(result.structured_content) == {"available", "reason", "suffixes"}


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="checks a machine without Linux")
async def test_a_run_that_cannot_happen_is_an_error_that_says_why(client):
    result = await client.call_tool("run_file", {"path": "app.py"})

    assert result.is_error
    assert result.content[0].text == "Error executing tool run_file: Running code needs Linux."


async def test_a_hidden_file_is_refused_before_anything_runs(client, tmp_path):
    (tmp_path / "home" / ".hidden.py").write_text("print(1)", encoding="utf-8")

    result = await client.call_tool("run_file", {"path": ".hidden.py"})

    assert result.is_error
    status = (await client.call_tool("runner_status", {})).structured_content
    if status["available"]:
        assert "Refused: " in result.content[0].text
