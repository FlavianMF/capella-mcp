"""Regression test that the MCP surface (tools + resource templates) is
wired up correctly. No Capella/Docker involved -- this only exercises the
MCPServer registration, not tool execution."""

from __future__ import annotations

from capella_mcp import server


def test_all_v1_tools_registered():
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert names == {
        "list_layers",
        "list_elements",
        "get_element",
        "create_element",
        "update_element",
        "create_diagram",
        "create_container_diagram",
        "delete_diagram",
        "export_diagram",
    }


def test_all_resource_templates_registered():
    templates = {t.uri_template for t in server.mcp._resource_manager.list_templates()}
    assert templates == {
        "capella://{+model_path}/layers",
        "capella://{+model_path}/layer/{layer}",
        "capella://{+model_path}/element/{element_id}",
        "capella://{+model_path}/diagrams",
        "capella://{+model_path}/diagram/{diagram_uid}",
    }
