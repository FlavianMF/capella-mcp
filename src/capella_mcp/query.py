"""Model query tools (PRD-09): find_references, trace_element,
list_exchanges, impact_analysis.

Read-only traversal over a capellambse model -- no Capella process, no
write, no save. Every function takes an already-open
`capellambse.MelodyModel` so it can be tested against in-memory models;
bridge.py opens the model (via fast_reader._open, under model_lock) and maps
errors to BridgeError.

Contract (capella_llm_window docs/prd/00-shared-contracts.md, "Model query
tools (PRD-09)"), shared with the live Java tools so both backends answer
with the same shape:

- arguments: element_id (required), max_results (default 50, clamped to
  1..200), max_depth where a walk is involved (default 2, clamped to 1..4);
- result: {root: {id, label, type, layer}, items: [QueryItem], total,
  truncated}, impact_analysis adds counts (items per relation, before the
  cut);
- QueryItem: {id, label, type, layer, relation, direction, depth, viaId};
  layer is oa|sa|la|pa|epbs or ""; direction is in|out or "";
- items deduplicated on (id, relation, direction), sorted by depth, layer
  (OA, SA, LA, PA, EPBS, then ""), type, label, id, then cut to max_results.

Python mirrors of the Java core: Limits/parse_limits = QueryLimits,
build_result = QueryResult.of(...).toJson(), walk = GraphWalker.walk.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

from capellambse.metamodel import cs, fa, modellingcore
from capellambse.model import Containment

from capella_mcp import fast_reader

DEFAULT_MAX_RESULTS = 50
MAX_RESULTS_CAP = 200
DEFAULT_MAX_DEPTH = 2
MAX_DEPTH_CAP = 4

LAYERS = ("oa", "sa", "la", "pa", "epbs")
_LAYER_ORDER = {layer: n for n, layer in enumerate(LAYERS)}

TRACE_RELATIONS = ("all", "allocation", "realization")
DIRECTIONS = ("both", "in", "out")
EXCHANGE_KINDS = ("all", "functional", "component", "physical")


class QueryError(ValueError):
    """A bad tool argument -- message is meant for the model to read."""


class Limits(NamedTuple):
    element_id: str
    max_results: int
    max_depth: int


def _int_arg(name: str, value: Any, default: int, cap: int) -> int:
    if value is None:
        return default
    # bool is an int subclass; True as a limit is a caller bug, not 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryError(f"{name} must be an integer, got {value!r}")
    return max(1, min(cap, value))


def parse_limits(element_id: Any, max_results: Any = None, max_depth: Any = None) -> Limits:
    if element_id is not None and not isinstance(element_id, str):
        raise QueryError(f"element_id must be a string, got {element_id!r}")
    if element_id is None or not element_id.strip():
        raise QueryError("element_id is required")
    return Limits(
        element_id=element_id,
        max_results=_int_arg("max_results", max_results, DEFAULT_MAX_RESULTS, MAX_RESULTS_CAP),
        max_depth=_int_arg("max_depth", max_depth, DEFAULT_MAX_DEPTH, MAX_DEPTH_CAP),
    )


def _choice(name: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise QueryError(f"{name} must be one of {', '.join(allowed)}; got {value!r}")
    return value


def _sort_key(item: dict) -> tuple:
    return (
        item["depth"],
        _LAYER_ORDER.get(item["layer"], len(LAYERS)),
        item["type"] or "",
        item["label"] or "",
        item["id"],
        # Not in the contract's sort list, but needed for byte-for-byte
        # determinism when one element appears under several relations.
        item["relation"],
        item["direction"],
    )


def build_result(root: dict, items: Iterable[dict], limits: Limits, with_counts: bool = False) -> dict:
    """Dedup on (id, relation, direction) keeping the first in sort order
    (so the shallowest), sort, count, cut. Byte-for-byte deterministic for
    the same input set regardless of input order."""
    unique: dict[tuple, dict] = {}
    for item in sorted(items, key=_sort_key):
        unique.setdefault((item["id"], item["relation"], item["direction"]), item)
    ordered = list(unique.values())
    result = {
        "root": root,
        "items": ordered[: limits.max_results],
        "total": len(ordered),
        "truncated": len(ordered) > limits.max_results,
    }
    if with_counts:
        counts = Counter(i["relation"] for i in ordered)
        result["counts"] = {rel: counts[rel] for rel in sorted(counts)}
    return result


Neighbors = Callable[[Any], Iterable[tuple[Any, str, str]]]


def walk(root: Any, neighbors: Neighbors, max_depth: int, cap: int) -> list[tuple[Any, str, str, int, str]]:
    """Breadth-first walk from root over neighbors(node) -> (node, relation,
    direction). Returns (node, relation, direction, depth, via_uuid) for
    every node reached within max_depth, root excluded, each node at most
    once (cycle-safe). Stops collecting at `cap` candidates; callers pass
    max_results * 2 so build_result's cut still sees a stable prefix."""
    seen = {root.uuid}
    found: list[tuple[Any, str, str, int, str]] = []
    queue = deque([(root, 0)])
    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for nxt, relation, direction in neighbors(node):
            if nxt.uuid in seen:
                continue
            seen.add(nxt.uuid)
            found.append((nxt, relation, direction, depth + 1, node.uuid))
            if len(found) >= cap:
                return found
            queue.append((nxt, depth + 1))
    return found


# --------------------------------------------------------------------------
# model helpers
# --------------------------------------------------------------------------


class _Ctx:
    """Per-call caches over one model (layer lookup, trace/exchange indexes)."""

    def __init__(self, model):
        self.model = model
        self._layer_roots: dict[str, str] = {}
        for layer in LAYERS:
            try:
                obj = getattr(model, layer, None)
            except Exception:
                obj = None
            if obj is not None:
                self._layer_roots[obj.uuid] = layer
        self._layer_cache: dict[str, str] = {}
        self._traces: list | None = None
        self._exchanges: list | None = None

    def layer_of(self, el) -> str:
        chain = []
        cur = el
        layer = ""
        while cur is not None:
            uuid = getattr(cur, "uuid", None)
            if uuid in self._layer_cache:
                layer = self._layer_cache[uuid]
                break
            if uuid in self._layer_roots:
                layer = self._layer_roots[uuid]
                break
            chain.append(uuid)
            try:
                cur = cur.parent
            except Exception:
                cur = None
        for uuid in chain:
            self._layer_cache[uuid] = layer
        return layer

    def ref(self, el) -> dict:
        return {
            "id": el.uuid,
            "label": fast_reader._label(el) or "",
            "type": fast_reader._type_name(el),
            "layer": self.layer_of(el),
        }

    def item(self, el, relation: str, direction: str, depth: int, via: str, type_: str | None = None) -> dict:
        ref = self.ref(el)
        if type_ is not None:
            ref["type"] = type_
        return {**ref, "relation": relation, "direction": direction, "depth": depth, "viaId": via}

    def traces(self) -> list:
        if self._traces is None:
            self._traces = [
                t
                for t in self.model.search()
                if isinstance(t, modellingcore.AbstractTrace) and t.source is not None and t.target is not None
            ]
        return self._traces

    def exchanges(self) -> list:
        """(exchange, kind, source_end, target_end) for every exchange."""
        if self._exchanges is None:
            out = []
            for ex in self.model.search(fa.FunctionalExchange, fa.ComponentExchange, cs.PhysicalLink):
                if isinstance(ex, cs.PhysicalLink):
                    ends = list(ex.ends)
                    if len(ends) != 2:
                        continue
                    out.append((ex, "physical", ends[0], ends[1]))
                else:
                    kind = "functional" if isinstance(ex, fa.FunctionalExchange) else "component"
                    if ex.source is None or ex.target is None:
                        continue
                    out.append((ex, kind, ex.source, ex.target))
            self._exchanges = out
        return self._exchanges


def _resolve(model, element_id: str):
    try:
        return model.by_uuid(element_id)
    except KeyError:
        raise fast_reader.NotFound(f"element not found: {element_id}") from None


def _trace_kind_matches(trace, relation: str) -> bool:
    if relation == "all":
        return True
    suffix = "Allocation" if relation == "allocation" else "Realization"
    return type(trace).__name__.endswith(suffix)


def _trace_neighbors(ctx: _Ctx, relation: str, direction: str) -> Neighbors:
    def neighbors(node):
        out = []
        for t in ctx.traces():
            if not _trace_kind_matches(t, relation):
                continue
            name = type(t).__name__
            if direction in ("out", "both") and t.source.uuid == node.uuid:
                out.append((t.target, name, "out"))
            if direction in ("in", "both") and t.target.uuid == node.uuid:
                out.append((t.source, name, "in"))
        return sorted(out, key=lambda n: (n[2], n[1], n[0].uuid))

    return neighbors


def _trace_items(ctx: _Ctx, root, relation: str, direction: str, max_depth: int, cap: int, label=None) -> list:
    found = walk(root, _trace_neighbors(ctx, relation, direction), max_depth, cap)
    return [ctx.item(n, label or rel, d, depth, via) for n, rel, d, depth, via in found]


def _is_containment(obj, attr: str) -> bool:
    return isinstance(getattr(type(obj), attr, None), Containment)


def _reference_items(ctx: _Ctx, root, relation_label: str | None = None) -> list:
    items = []
    for obj, attr, _index in ctx.model.find_references(root):
        if not hasattr(obj, "uuid") or obj.uuid == root.uuid or _is_containment(obj, attr):
            continue
        items.append(ctx.item(obj, relation_label or attr, "in", 1, root.uuid))
    return items


def _diagram_layer(ctx: _Ctx, diagram) -> str:
    try:
        target = diagram.target
    except Exception:
        target = None
    return ctx.layer_of(target) if target is not None else ""


def _diagrams_showing(ctx: _Ctx, uuids: set[str]) -> list[tuple[Any, str]]:
    """(diagram, uuid of the shown element) for diagrams with a node whose
    semantic element is in uuids."""
    found = []
    for diagram in ctx.model.diagrams:
        try:
            nodes = diagram.nodes
        except Exception:
            # An unrenderable diagram is skipped, not a failed query.
            continue
        for node in nodes:
            uuid = getattr(node, "uuid", None)
            if uuid in uuids:
                found.append((diagram, uuid))
                break
    return found


def _diagram_items(ctx: _Ctx, root, uuids: set[str], relation: str) -> list:
    items = []
    for diagram, shown in _diagrams_showing(ctx, uuids):
        item = ctx.item(diagram, relation, "", 1, shown, type_="Diagram")
        item["layer"] = _diagram_layer(ctx, diagram)
        items.append(item)
    return items


def _owner(end):
    """The element an exchange end belongs to: a port's owner, a Part's
    type, else the end itself."""
    if type(end).__name__.endswith("Port"):
        try:
            return end.parent
        except Exception:
            return end
    if type(end).__name__ == "Part":
        try:
            return end.type or end
        except Exception:
            return end
    return end


def _endpoint_ids(root) -> set[str]:
    ids = {root.uuid}
    for attr in ("ports", "physical_ports", "inputs", "outputs", "representing_parts"):
        try:
            values = getattr(root, attr, None) or []
            ids.update(v.uuid for v in values if hasattr(v, "uuid"))
        except Exception:
            continue
    return ids


def _exchange_items(ctx: _Ctx, root, kind: str, relation_label: str | None = None) -> list:
    ids = _endpoint_ids(root)
    items = []
    for ex, ex_kind, src, tgt in ctx.exchanges():
        if kind != "all" and ex_kind != kind:
            continue
        if src.uuid in ids:
            direction, other = "out", tgt
        elif tgt.uuid in ids:
            direction, other = "in", src
        else:
            continue
        items.append(ctx.item(ex, relation_label or ex_kind, direction, 1, _owner(other).uuid))
    return items


def _contained_items(ctx: _Ctx, root, max_depth: int) -> list:
    items = []
    for el in ctx.model.search(below=root):
        # depth = parent hops back to root; walking stops past max_depth.
        depth, cur = 0, el
        while cur is not None and cur.uuid != root.uuid and depth <= max_depth:
            depth += 1
            cur = cur.parent
        if cur is None or depth == 0 or depth > max_depth:
            continue
        items.append(ctx.item(el, "contained", "", depth, el.parent.uuid))
    return items


# --------------------------------------------------------------------------
# the four tools
# --------------------------------------------------------------------------


def find_references(model, element_id, max_results=None, include_diagrams: bool = False) -> dict:
    limits = parse_limits(element_id, max_results)
    ctx = _Ctx(model)
    root = _resolve(model, limits.element_id)
    items = _reference_items(ctx, root)
    if include_diagrams:
        items += _diagram_items(ctx, root, {root.uuid}, "shown_in")
    return build_result(ctx.ref(root), items, limits)


def trace_element(model, element_id, relation: str = "all", direction: str = "both", max_depth=None, max_results=None) -> dict:
    limits = parse_limits(element_id, max_results, max_depth)
    _choice("relation", relation, TRACE_RELATIONS)
    _choice("direction", direction, DIRECTIONS)
    ctx = _Ctx(model)
    root = _resolve(model, limits.element_id)
    items = _trace_items(ctx, root, relation, direction, limits.max_depth, limits.max_results * 2)
    return build_result(ctx.ref(root), items, limits)


def list_exchanges(model, element_id, kind: str = "all", max_results=None) -> dict:
    limits = parse_limits(element_id, max_results)
    _choice("kind", kind, EXCHANGE_KINDS)
    ctx = _Ctx(model)
    root = _resolve(model, limits.element_id)
    return build_result(ctx.ref(root), _exchange_items(ctx, root, kind), limits)


def impact_analysis(model, element_id, max_depth=None, max_results=None) -> dict:
    limits = parse_limits(element_id, max_results, max_depth)
    ctx = _Ctx(model)
    root = _resolve(model, limits.element_id)
    contained = _contained_items(ctx, root, limits.max_depth)
    items = list(contained)
    items += _reference_items(ctx, root, "referenced_by")
    items += _trace_items(ctx, root, "all", "both", 1, MAX_RESULTS_CAP * 2, label="trace")
    items += _exchange_items(ctx, root, "all", "exchange")
    items += _diagram_items(ctx, root, {root.uuid} | {i["id"] for i in contained}, "diagram")
    return build_result(ctx.ref(root), items, limits, with_counts=True)
