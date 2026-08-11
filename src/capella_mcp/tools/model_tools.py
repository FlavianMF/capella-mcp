"""MCP tools for reading and writing a Capella model.

See docs/architecture.md for the full tool/resource surface and
docs/decisions/0004-escopo-v1-leitura-e-escrita.md for why write tools are
already in v1. Any exception raised here (e.g. bridge.BridgeError) is
turned by the MCP SDK into a CallToolResult(is_error=True) automatically --
no need to catch and re-wrap.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from capella_mcp import bridge


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def list_layers(model_path: str) -> dict:
        """List the Arcadia layers (OA/SA/LA/PA/EPBS) present in a Capella model."""
        return bridge.list_layers(model_path)

    @mcp.tool()
    def list_elements(model_path: str, layer: str, type_filter: str | None = None) -> dict:
        """List elements in one Arcadia layer of a Capella model.

        layer must be one of: oa, sa, la, pa, epbs. type_filter, if given, is
        a Capella metamodel type name (e.g. "LogicalComponent").
        """
        return bridge.list_elements(model_path, layer, type_filter)

    @mcp.tool()
    def get_element(model_path: str, element_id: str) -> dict:
        """Get a single element by id from a Capella model."""
        return bridge.get_element(model_path, element_id)

    @mcp.tool()
    def create_element(
        model_path: str,
        layer: str,
        type_name: str,
        name: str,
        parent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict:
        """Create a new element in a Capella model and save the model.

        layer must be one of: oa, sa, la, pa, epbs. type_name is a Capella
        metamodel class name (e.g. "LogicalComponent"). If parent_id is
        omitted, the element is added directly under the layer's root.
        """
        return bridge.create_element(model_path, layer, type_name, name, parent_id, attributes)

    @mcp.tool()
    def update_element(model_path: str, element_id: str, attributes: dict[str, Any]) -> dict:
        """Update attributes of an existing element in a Capella model and save the model."""
        return bridge.update_element(model_path, element_id, attributes)
