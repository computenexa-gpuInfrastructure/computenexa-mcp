"""ComputeNexa MCP server: let your AI assistant find, launch and stop private GPUs on ComputeNexa.

    uvx computenexa-mcp            # with CNX_API_KEY set: a stdio MCP server named "ComputeNexa"

Guide: https://app.computenexa.com/help/mcp
"""

from ._version import __version__
from .server import build_server, main

__all__ = ["__version__", "build_server", "main"]
