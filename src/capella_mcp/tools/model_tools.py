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
    def list_diagrams(model_path: str) -> dict:
        """List every diagram already saved in a Capella model, with uid, name
        and diagram type -- check this before deciding whether to create a new
        diagram or an existing one already satisfies the request.
        """
        return bridge.list_diagrams(model_path)

    @mcp.tool()
    def get_diagram(model_path: str, diagram_uid: str) -> dict:
        """Get the elements currently placed in one existing diagram by uid
        (from list_diagrams, or "diagram_uid"/"uid" returned by a previous
        create_*_diagram call).
        """
        return bridge.get_diagram(model_path, diagram_uid)

    @mcp.tool()
    def add_to_diagram(
        model_path: str, diagram_uid: str, element_id: str, parent_element_id: str | None = None
    ) -> dict:
        """Add an existing model element to an existing diagram, without
        recreating the diagram or touching any node already in it.

        Use this right after create_element when the new element should be
        visible somewhere the user already looks -- create_element itself
        never touches any diagram, and every create_*_diagram tool always
        (re)creates a brand new diagram (duplicating, not updating, if one
        with that content already exists). Call list_diagrams first to find
        a candidate diagram_uid.

        diagram_uid must be an existing diagram. element_id must already
        exist in the model and be a type this diagram's Sirius mapping
        accepts:
          - Breakdown diagrams (create_diagram): element_id must be a
            descendant of THIS diagram's own root -- parent_element_id is
            not applicable (breakdown diagrams are flat, never nested).
            Capella auto-synchronizes a breakdown diagram's DIRECT children
            on every save regardless of this call -- add_to_diagram mainly
            matters here for elements beyond the diagram's original
            max_depth, or added after the diagram already existed.
          - Container/Class/Capability Blank diagrams
            (create_container_diagram, create_class_diagram,
            create_capability_diagram): element_id must match the diagram's
            accepted types (Entity/Actor, DataPkg/Class, or
            Entity/Actor/OperationalCapability respectively). Adding an
            Entity/Actor or OperationalCapability to an Operational
            Capabilities Blank also auto-creates involvement edges to
            whichever of the other kind are already present in the diagram
            (mirrors create_capability_diagram's own default). Class
            elements must pass parent_element_id (an existing DataPkg node
            in this diagram); parent_element_id is optional for
            DataPkg/Entity/Actor (omit to add as a new top-level node), and
            not applicable for OperationalCapability (always a free node).
          - Scenario diagrams (create_scenario_diagram) are NOT supported --
            their content is ordered InstanceRoles/SequenceMessages, not
            simple containment; build them via create_element instead.

        Any unsupported (diagram_type, element_type) combination raises an
        error naming exactly what's wrong -- never silently returns success
        without the element actually appearing. If element_id is already
        present, returns {"already_present": true, ...} instead of
        duplicating.

        Only positions the NEW node (appended past the diagram's current
        bounding box) -- existing nodes are never moved. Call
        layout_diagram afterward if you want the whole diagram rearranged.
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
            return bridge.add_to_diagram(model_path, diagram_uid, element_id, parent_element_id)

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

        Locked (bridge.model_lock) for the whole call so a concurrent
        fast_reader read never observes a partially-written model -- see
        docs/decisions/0005-camada-leitura-capellambse.md.

        Only these type_names have a validated containment path and will
        actually persist: LogicalComponent, SystemFunction, LogicalFunction,
        OperationalActivity, OperationalActor, OperationalEntity,
        OperationalCapability, StateMachine (parent_id = a Component),
        Region (parent_id = a StateMachine), State/Mode (parent_id = a
        Region), DataPkg/Class (parent_id = an existing DataPkg -- every
        layer already has a default one), FunctionalExchange (parent_id =
        the owning Function -- typically the nearest common ancestor of
        source/target; attributes must include source_id/target_id, the
        element ids of two existing Functions of the same kind as the
        layer, e.g. two OperationalActivity ids for layer="oa" -- a
        FunctionOutputPort/FunctionInputPort pair is created on them and
        wired to the exchange automatically), Scenario (parent_id = an
        existing OperationalCapability), InstanceRole (parent_id = an
        existing Scenario; attributes must include represented_instance_id,
        the element id of an existing Entity/Actor/Function to represent),
        SequenceMessage (parent_id = an existing Scenario; attributes must
        include source_id/target_id, the element ids of two existing
        InstanceRoles in that Scenario, and may include kind --
        "SYNCHRONOUS_CALL" (default) or "ASYNCHRONOUS_CALL"). Any other
        type_name silently fails to persist -- the call returns success
        with a valid id, but the element never actually appears in the
        model on a later call.

        A newly created element never appears in any diagram by itself --
        call list_diagrams and then add_to_diagram(diagram_uid, element_id)
        to make it visible somewhere the user already looks.
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
            return bridge.create_element(model_path, layer, type_name, name, parent_id, attributes)

    @mcp.tool()
    def update_element(model_path: str, element_id: str, attributes: dict[str, Any]) -> dict:
        """Update attributes of an existing element in a Capella model and save the model.

        Locked (bridge.model_lock) for the whole call -- see
        docs/decisions/0005-camada-leitura-capellambse.md.
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
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
        """Create a real Capella (Sirius) breakdown diagram for ONE element's
        subtree and save the model -- fully automatic, including layout
        (python4capella has no auto-arrange, so bounds are computed and
        written explicitly).

        Use this when the request is about the breakdown/hierarchy of one
        specific element and its children -- NOT for "show all X in the
        layer" as a whole set (use create_container_diagram), not for an
        overview with Operational Capabilities (use
        create_capability_diagram), and not for an exact subset of elements
        or an interaction between them (use create_scenario_diagram). Call
        list_diagrams first to check a suitable diagram doesn't already
        exist.

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
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
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
        the ContainerMapping-based diagram family, distinct from
        create_diagram's NodeMapping-based "breakdown" trees.

        Use this when the request is "show all Entities/Actors/Activities of
        the layer" as a whole set -- always includes every root-level
        element of that type, cannot filter to a subset. For "only X and Y",
        use create_scenario_diagram instead; for one element's own subtree,
        use create_diagram. Call list_diagrams first to check a suitable
        diagram doesn't already exist.

        layer must be one of: oa, sa, la, pa, epbs. Supported (layer,
        type_name) combinations: OperationalEntity/oa and OperationalActor/oa
        ("Operational Entity Blank" -- containment only, no edges) and
        OperationalActivity/oa ("Operational Activity Interaction Blank" --
        also adds functional-exchange edges between the placed activities).
        Unlike create_diagram, there is no root_id -- this always covers the
        WHOLE forest of root-level elements of that type in the layer (a
        Blank diagram shows a package's entire structure, not one element's
        subtree).

        Renders correctly in export_diagram's headless PNG (see
        CONTAINER_DIAGRAMS' comment in bridge.py for the root-cause
        investigation of the blank-render bug this used to have, and the
        fix -- DiagramServices.createContainer() instead of
        python4capella's apply_mapping()).
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
            return bridge.create_container_diagram(model_path, layer, type_name, diagram_name, max_depth)

    @mcp.tool()
    def create_class_diagram(
        model_path: str,
        layer: str,
        diagram_name: str | None = None,
        max_depth: int | None = None,
    ) -> dict:
        """Create a real Capella (Sirius) "Class Diagram Blank" and save the
        model, rooted at the layer's own default DataPkg -- recurses through
        nested DataPkgs and every Class owned along the way.

        Use this only for the data model (DataPkg/Class) -- never for
        Entities, Actors, Activities, Functions or Components; none of the
        other 4 diagram tools cover DataPkg/Class. Call list_diagrams first
        to check a suitable diagram doesn't already exist.

        layer must be one of: oa, sa, la, pa, epbs (every layer has a
        DataPkg). To populate it first, create_element supports type_name
        "DataPkg" (parent_id = an existing DataPkg) and "Class" (parent_id =
        an existing DataPkg).

        Renders correctly in export_diagram's headless PNG (see
        CONTAINER_DIAGRAMS' comment in bridge.py for the root-cause
        investigation and fix).
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
            return bridge.create_class_diagram(model_path, layer, diagram_name, max_depth)

    @mcp.tool()
    def create_capability_diagram(model_path: str, diagram_name: str | None = None) -> dict:
        """Create a real Capella (Sirius) "Operational Capabilities Blank"
        (OCB) diagram and save the model.

        Use this for an OA overview of Entities/Actors together with their
        Operational Capabilities -- it overlaps with
        create_container_diagram's OperationalEntity/Actor combo but
        additionally nests each entity's involved capabilities inside its
        container; use create_container_diagram instead if capabilities
        don't need to be shown. Call list_diagrams first to check a
        suitable diagram doesn't already exist.

        OA-layer only. Covers the whole forest of root Operational
        Entities/Actors (like create_container_diagram's OperationalEntity
        combination), and additionally nests each entity's involved
        Operational Capabilities inside its container. A capability
        involved by more than one entity appears once per involving entity
        (valid Sirius behavior, not a duplicate bug).

        Renders correctly in export_diagram's headless PNG (see
        CONTAINER_DIAGRAMS' comment in bridge.py for the root-cause
        investigation and fix).
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
            return bridge.create_capability_diagram(model_path, diagram_name)

    @mcp.tool()
    def create_scenario_diagram(
        model_path: str, scenario_id: str, scenario_kind: str = "OES", diagram_name: str | None = None
    ) -> dict:
        """Create a real Capella (Sirius) sequence/scenario diagram and save
        the model -- OES ("Operational Interaction Scenario") or OAS
        ("Activity Interaction Scenario"), OA-layer only.

        Use this when the request names an EXACT subset of elements to
        appear together (e.g. "only X and Y"), or an interaction/sequence
        between them -- this is the only diagram tool that can restrict
        which elements are shown, at the cost of first building a
        Scenario/InstanceRoles/SequenceMessages via create_element (see that
        tool's docstring). For the whole set of a type, use
        create_container_diagram instead. Call list_diagrams first to check
        a suitable diagram doesn't already exist.

        scenario_id must be an existing Scenario -- build it, its
        InstanceRoles, and its SequenceMessages first via create_element
        (see that tool's docstring). scenario_kind is "OES" (default,
        InstanceRoles represent Entities/Actors) or "OAS" (InstanceRoles
        represent OperationalActivities).

        Renders correctly in export_diagram's headless PNG (see the
        _SCENARIO_DIAGRAM_MAPPINGS/create_scenario_diagram comment in
        bridge.py for the root-cause investigation and fix -- this diagram
        family needed a different fix than every other diagram type in this
        module, an explicit re-run of Sirius's own sequence-diagram
        ordering-repair chain).
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
            return bridge.create_scenario_diagram(model_path, scenario_id, scenario_kind, diagram_name)

    @mcp.tool()
    def delete_diagram(model_path: str, diagram_uid: str) -> dict:
        """Delete a diagram from a Capella model and save the model.

        diagram_uid is the value returned as "diagram_uid" by create_diagram
        or "uid" by the capella://{model_path}/diagrams resource.
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
            return bridge.delete_diagram(model_path, diagram_uid)

    @mcp.tool()
    def layout_diagram(model_path: str, diagram_uid: str) -> dict:
        """Apply Capella's native 'Layout > All' to rearrange all elements in
        an existing diagram, same as right-click -> Layout -> All.

        Works for all diagram types (breakdown, container, class, capability,
        scenario). Uses Sirius's own layout pipeline so it respects pinned
        elements and registered layout providers. Falls back to a custom
        deterministic tree layout when the native pipeline is unavailable.

        diagram_uid is the value returned as "diagram_uid" by create_diagram
        or "uid" by the capella://{model_path}/diagrams resource.
        """
        with bridge.model_lock(bridge.resolve_model_path(model_path)):
            return bridge.layout_diagram(model_path, diagram_uid)

    @mcp.tool()
    def export_diagram(model_path: str, image_format: str = "PNG") -> dict:
        """Export every diagram in the model to image files (via Capella's
        native headless export, not an addon), saved next to the model in a
        `<model>_diagram_exports/` folder."""
        return bridge.export_diagram(model_path, image_format)
