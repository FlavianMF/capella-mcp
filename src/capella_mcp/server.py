"""Entry point for the Capella MCP server.

Tools and resources are registered in capella_mcp.tools / capella_mcp.resources
(Fase 5). See docs/architecture.md for the overall flow.
"""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("capella-mcp")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
