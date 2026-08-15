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

        Only these type_names have a validated containment path and will
        actually persist: LogicalComponent, SystemFunction, LogicalFunction,
        OperationalActivity, OperationalActor, OperationalEntity,
        OperationalCapability, StateMachine (parent_id = a Component),
        Region (parent_id = a StateMachine), State/Mode (parent_id = a
        Region). Any other type_name silently fails to persist -- the call
        returns success with a valid id, but the element never actually
        appears in the model on a later call.
        """
        return bridge.create_element(model_path, layer, type_name, name, parent_id, attributes)

    @mcp.tool()
    def update_element(model_path: str, element_id: str, attributes: dict[str, Any]) -> dict:
        """Update attributes of an existing element in a Capella model and save the model."""
        return bridge.update_element(model_path, element_id, attributes)

    @mcp.tool()
    def create_diagram(
        model_path: str,
        layer: str,
        type_name: str | None = None,
        root_id: str | None = None,
        include_relations: bool = True,
        diagram_name: str | None = None,
        max_depth: int | None = None,
    ) -> dict:
        """Create a real Capella (Sirius) breakdown diagram for one element's
        subtree and save the model -- fully automatic, including layout
        (python4capella has no auto-arrange, so bounds are computed and
        written explicitly).

        layer must be one of: oa, sa, la, pa, epbs. Supported (layer,
        type_name) combinations: OperationalActivity/oa, OperationalEntity/oa,
        OperationalActor/oa, SystemFunction/sa, LogicalFunction/la,
        LogicalComponent/la, PhysicalFunction/pa, PhysicalComponent/pa,
        ConfigurationItem/epbs, Region/la (a "Mode State Machine" diagram of
        that Region's direct States/Modes -- root_id is required for this
        one, there's no layer-wide package to auto-resolve a Region from;
        include_relations does not yet add StateTransition edges for this
        combination, states-only for now). Either pass root_id
        (the diagram covers that element's subtree, type_name inferred) or
        type_name with no root_id (only valid if exactly one root-level
        element of that type exists in the layer). include_relations also
        adds functional-exchange edges found among the placed elements.
        """
        return bridge.create_diagram(
            model_path, layer, type_name, root_id, include_relations, diagram_name, max_depth
        )

    @mcp.tool()
    def create_container_diagram(
        model_path: str,
        layer: str,
        type_name: str,
        diagram_name: str | None = None,
        max_depth: int | None = None,
    ) -> dict:
        """Create a real Capella (Sirius) "Blank" diagram and save the model --
        the ContainerMapping-based diagram family (currently only
        "Operational Entity Blank"), distinct from create_diagram's
        NodeMapping-based "breakdown" trees.

        layer must be one of: oa, sa, la, pa, epbs. Supported (layer,
        type_name) combinations: OperationalEntity/oa, OperationalActor/oa.
        Unlike create_diagram, there is no root_id -- this always covers the
        WHOLE forest of root-level elements of that type in the layer (a
        Blank diagram shows a package's entire structure, not one element's
        subtree).

        KNOWN LIMITATION: the diagram's node/containment data is fully
        correct (get_diagram/list_elements see it fine), but
        export_diagram's headless PNG for it will be blank -- a confirmed
        headless Sirius/GMF rendering gap for this whole diagram family
        (tested live against multiple unrelated domains, not specific to
        Entity), not a bug in this server. The diagram is still real,
        valid Capella model content and would plausibly render normally if
        the model is later opened in the interactive Capella desktop.
        """
        return bridge.create_container_diagram(model_path, layer, type_name, diagram_name, max_depth)

    @mcp.tool()
    def delete_diagram(model_path: str, diagram_uid: str) -> dict:
        """Delete a diagram from a Capella model and save the model.

        diagram_uid is the value returned as "diagram_uid" by create_diagram
        or "uid" by the capella://{model_path}/diagrams resource.
        """
        return bridge.delete_diagram(model_path, diagram_uid)

    @mcp.tool()
    def export_diagram(model_path: str, image_format: str = "PNG") -> dict:
        """Export every diagram in the model to image files (via Capella's
        native headless export, not an addon), saved next to the model in a
        `<model>_diagram_exports/` folder."""
        return bridge.export_diagram(model_path, image_format)
