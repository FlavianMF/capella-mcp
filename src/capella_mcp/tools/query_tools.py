"""PRD-09 model query tools: follow links between elements instead of
guessing them from names. All read-only and bounded; served from the saved
model files via capellambse (see bridge._run_query and capella_mcp.query).

Names, arguments, bounds and result shape follow the shared contract with
capella_llm_window's live tools ("Model query tools (PRD-09)"), so both
backends answer the same way. Every result is
{root: {id, label, type, layer}, items: [{id, label, type, layer, relation,
direction, depth, viaId}], total, truncated}; when truncated is true the
answer is partial and total says how many matched.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from capella_mcp import bridge


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def find_references(
        model_path: str, element_id: str, max_results: int = 50, include_diagrams: bool = False
    ) -> dict:
        """List every element that references element_id through a
        non-containment reference (allocations, traces, typed parts...).
        Each item's relation is the referencing attribute name, direction
        "in". include_diagrams adds the diagrams showing the element (type
        "Diagram", relation "shown_in"). max_results default 50, max 200.
        """
        return bridge.find_references(model_path, element_id, max_results, include_diagrams)

    @mcp.tool()
    def trace_element(
        model_path: str,
        element_id: str,
        relation: str = "all",
        direction: str = "both",
        max_depth: int = 2,
        max_results: int = 50,
    ) -> dict:
        """Follow Capella traces (allocations, realizations) from element_id.
        relation: "allocation", "realization" or "all". direction: "out"
        (element is the trace source, e.g. a component allocating a
        function, or a lower-layer element realizing an upper one), "in"
        (element is the target, e.g. "what allocates this function?") or
        "both". Each item's relation is the trace class name (e.g.
        ComponentFunctionalAllocation, FunctionRealization). max_depth
        default 2, max 4; max_results default 50, max 200.
        """
        return bridge.trace_element(model_path, element_id, relation, direction, max_depth, max_results)

    @mcp.tool()
    def list_exchanges(model_path: str, element_id: str, kind: str = "all", max_results: int = 50) -> dict:
        """List the exchanges whose source or target is element_id or one of
        its ports. kind: "functional", "component", "physical" or "all".
        Each item is the exchange; relation is its kind, direction "out" or
        "in", viaId the element at the other end. max_results default 50,
        max 200.
        """
        return bridge.list_exchanges(model_path, element_id, kind, max_results)

    @mcp.tool()
    def impact_analysis(model_path: str, element_id: str, max_depth: int = 2, max_results: int = 50) -> dict:
        """What a delete of element_id would touch, in one call. Items by
        relation: contained (owned elements, walked to max_depth),
        referenced_by, trace (allocations/realizations, both directions),
        exchange, diagram (diagrams showing it or a contained element).
        counts gives items per relation before the max_results cut.
        max_depth default 2, max 4; max_results default 50, max 200.
        """
        return bridge.impact_analysis(model_path, element_id, max_depth, max_results)
