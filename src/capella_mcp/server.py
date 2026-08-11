"""Entry point for the Capella MCP server.

See docs/architecture.md for the overall flow and docs/decisions/ for why
it's shaped this way.
"""

from mcp.server.mcpserver import MCPServer

from capella_mcp.resources.model_resources import register as register_resources
from capella_mcp.tools.model_tools import register as register_tools

mcp = MCPServer("capella-mcp")
register_tools(mcp)
register_resources(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
