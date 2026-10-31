"""The installed `computenexa-mcp` command speaks MCP over stdio (no API key: tools explain how to set one)."""

import json
import os
import pathlib
import re
import shutil
import sys

import anyio
import pytest
from mcp import Client, StdioServerParameters

from computenexa_mcp import __version__


def test_the_command_starts_and_answers_over_stdio():
    command = shutil.which("computenexa-mcp", path=os.path.dirname(sys.executable))
    if command is None:
        pytest.skip("the package isn't installed as a command in this environment")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CNX_")}

    async def go():
        async with Client(StdioServerParameters(command=command, env=env)) as session:
            names = {t.name for t in (await session.list_tools()).tools}
            reply = await session.call_tool("whoami", {})
            return session.server_info, names, reply

    info, names, reply = anyio.run(go)
    assert info.name == "ComputeNexa" and info.version == __version__
    assert "launch_machine" in names and len(names) == 15
    assert reply.is_error and "CNX_API_KEY" in reply.content[0].text


def test_versions_agree():
    root = pathlib.Path(__file__).resolve().parent.parent
    project = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.MULTILINE).group(1)
    server = json.loads((root / "server.json").read_text())
    assert project == __version__ == server["version"] == server["packages"][0]["version"]
    assert f"mcp-name: {server['name']}" in (root / "README.md").read_text()
