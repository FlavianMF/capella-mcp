"""MCP resources for read-only navigation of a Capella model.

See docs/concepts/arcadia-layers.md for the layer -> resource URI mapping.
`{+model_path}` (RFC 6570 reserved expansion) is used instead of
`{model_path}` so multi-segment paths under the models root keep their `/`.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from capella_mcp import bridge


def register(mcp: MCPServer) -> None:
    @mcp.resource("capella://{+model_path}/layers")
    def layers(model_path: str) -> dict:
        return bridge.list_layers(model_path)

    @mcp.resource("capella://{+model_path}/layer/{layer}")
    def layer(model_path: str, layer: str) -> dict:
        return bridge.list_elements(model_path, layer)

    @mcp.resource("capella://{+model_path}/element/{element_id}")
    def element(model_path: str, element_id: str) -> dict:
        return bridge.get_element(model_path, element_id)
