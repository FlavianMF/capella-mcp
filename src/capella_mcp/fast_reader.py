"""Fast, pure-Python read path for a Capella model, via `capellambse`.

Unlike bridge.py (which shells out to a full Capella headless process --
see docs/decisions/0002-headless-por-chamada.md), capellambse parses the
.aird/.capella fragments directly (no JVM, no Xvfb, no Eclipse workspace
machinery) in milliseconds. It cannot touch Sirius diagrams or write
safely alongside them, so it's only used here for the read-only tools
(list_layers/list_elements/get_element) -- see
docs/decisions/0005-camada-leitura-capellambse.md for the full rationale.

Every public function here returns the exact same shape bridge.py's
headless equivalent does, so bridge.py's dispatcher can use either
interchangeably and tools/model_tools.py never needs to know which path
served a given call.

NotFound is the one exception this module raises that the dispatcher
must NOT treat as "capellambse can't do this, fall back to headless" --
it means the element/layer genuinely doesn't exist, and re-confirming
that via a ~1min headless call would be pure waste. Every other
exception (MissingClassError for an unknown type_filter, AttributeError
for a metamodel shape this module doesn't handle yet, etc.) is a real
coverage gap and must propagate so the dispatcher falls back.
"""

from __future__ import annotations

from pathlib import Path

import capellambse
from capellambse.metamodel import cs, fa, oa

_LAYER_ATTRS = {
    "oa": "oa",
    "sa": "sa",
    "la": "la",
    "pa": "pa",
    "epbs": "epbs",
}


class NotFound(Exception):
    """The requested layer/element genuinely does not exist in the model."""


# python4capella (the headless path) wraps the single EMF oa "Entity" class
# in two Python classes, OperationalActor and OperationalEntity, split on the
# Entity's actor flag; capellambse exposes one oa.Entity with `is_actor`.
# Map both ways so a type_filter and a reported "type" mean the same thing on
# either path (see bridge._list_elements_headless's own exact-class-name
# filter for the headless half of this).
_SPLIT_TYPES = {
    "OperationalActor": ("Entity", True),
    "OperationalEntity": ("Entity", False),
}


def _type_name(el) -> str:
    name = type(el).__name__
    if name == "Entity" and hasattr(el, "is_actor"):
        return "OperationalActor" if el.is_actor else "OperationalEntity"
    return name


def _serialize(el) -> dict:
    # el.name is the raw NamedElement attribute; python4capella's
    # get_label() (the headless side's equivalent) goes through Capella's
    # own label-provider service instead, which for a handful of types
    # (observed live for OES InstanceRoles, see the "OA 2" placeholder bug
    # documented in bridge.py's create_element/InstanceRole branch) can
    # diverge from the raw name. Good enough for the common NamedElement
    # case this module targets; tests/test_fast_reader.py's parity checks
    # are what would catch a real divergence for a given type.
    return {
        "id": el.uuid,
        "label": getattr(el, "name", None),
        "type": _type_name(el),
    }


def _open(abs_path: Path) -> capellambse.MelodyModel:
    return capellambse.MelodyModel(str(abs_path))


def _layer_or_none(model: capellambse.MelodyModel, attr: str):
    # Not verified against a real model that's missing a layer (both
    # fixture models have all 5) -- AttributeError/None are both treated
    # as "absent" defensively. If neither ever actually fires here in
    # practice, headless's own `layer is not None` check is the one
    # that's been live-verified (see LAYER_METHODS in bridge.py).
    try:
        return getattr(model, attr, None)
    except AttributeError:
        return None


def list_layers(abs_path: Path) -> dict:
    model = _open(abs_path)
    layers = [
        {"layer": key, "present": _layer_or_none(model, attr) is not None}
        for key, attr in _LAYER_ATTRS.items()
    ]
    return {"layers": layers}


def list_elements(abs_path: Path, layer: str, type_filter: str | None) -> dict:
    if layer not in _LAYER_ATTRS:
        raise NotFound(f"unknown layer {layer!r}")
    if type_filter is None:
        # Headless's own get_contents() (no type_filter) returns the
        # layer's *direct* EMF children (its Pkg containers -- entity_pkg,
        # function_pkg, etc.), not the domain elements inside them. There
        # is no equivalent one-call capellambse API for that shallow EMF
        # eContents() shape, and emulating it isn't worth the risk of a
        # subtly wrong result for a call pattern that's rarely the useful
        # one anyway (real usage passes type_filter). Let the dispatcher
        # fall back to headless for this specific case.
        raise NotImplementedError("list_elements without type_filter has no fast-path equivalent")

    model = _open(abs_path)
    layer_obj = _layer_or_none(model, _LAYER_ATTRS[layer])
    if layer_obj is None:
        raise NotFound(f"layer not present in model: {layer!r}")

    split = _SPLIT_TYPES.get(type_filter)
    if split is not None:
        base, is_actor = split
        elements = [
            el for el in model.search(base, below=layer_obj) if bool(getattr(el, "is_actor", False)) == is_actor
        ]
    else:
        elements = model.search(type_filter, below=layer_obj)
    return {"elements": [_serialize(el) for el in elements]}


# Per-list cap for get_element's "relations": keeps one call on a heavily
# connected element (a root component allocating hundreds of functions)
# from flooding a small model's context. The full, paged view of a link set
# is what the query tools (tools/query_tools.py) are for.
RELATION_CAP = 25

# (metamodel class, ((capellambse attribute, relation key), ...)). Checked in
# order with isinstance; an element matching several classes gets the union.
# Keys are the wire names; the capellambse attribute is an implementation
# detail. Hand-picked rather than generic so `owner` (the allocating
# component of a function, but the *parent* of an Entity) can't leak in with
# the wrong meaning.
_RELATIONS = (
    (oa.OperationalCapability, (("involved_entities", "involved_entities"), ("involved_activities", "involved_activities"))),
    (oa.Entity, (("capabilities", "involved_capabilities"), ("activities", "allocated_activities"), ("related_exchanges", "exchanges"))),
    (
        fa.AbstractFunction,
        (
            ("owner", "allocated_to"),
            ("related_exchanges", "exchanges"),
            ("realized_functions", "realized_functions"),
            ("realizing_functions", "realizing_functions"),
        ),
    ),
    (
        cs.Component,
        (
            ("allocated_functions", "allocated_functions"),
            ("realized_components", "realized_components"),
            ("realizing_components", "realizing_components"),
        ),
    ),
    # SA Capability / LA-PA CapabilityRealization: no shared base class
    # worth importing, so matched by duck-typing in _relations().
)
_CAPABILITY_RELATIONS = (("involved_components", "involved_components"), ("involved_functions", "involved_functions"))


def _ref(el) -> dict:
    return {"id": el.uuid, "label": getattr(el, "name", None), "type": _type_name(el)}


def _targets(el, attr: str) -> list:
    try:
        value = getattr(el, attr)
    except Exception:
        # A capellambse accessor that can't resolve on this element/version
        # is a missing relation, not a failed get_element.
        return []
    if value is None:
        return []
    if hasattr(value, "uuid"):
        return [value]
    return [v for v in value if hasattr(v, "uuid")]


def _relations(el) -> tuple[dict, dict]:
    pairs: list[tuple[str, str]] = []
    for cls, attrs in _RELATIONS:
        if isinstance(el, cls):
            pairs.extend(attrs)
    if not isinstance(el, oa.OperationalCapability) and hasattr(type(el), "involved_components"):
        pairs.extend(_CAPABILITY_RELATIONS)

    relations: dict[str, list] = {}
    truncated: dict[str, int] = {}
    for attr, key in pairs:
        seen: dict[str, dict] = {}
        for target in _targets(el, attr):
            seen.setdefault(target.uuid, _ref(target))
        if not seen:
            continue
        refs = sorted(seen.values(), key=lambda r: (r["label"] or "", r["id"]))
        if len(refs) > RELATION_CAP:
            truncated[key] = len(refs)
            refs = refs[:RELATION_CAP]
        relations[key] = refs
    return relations, truncated


def get_element(abs_path: Path, element_id: str) -> dict:
    model = _open(abs_path)
    try:
        el = model.by_uuid(element_id)
    except KeyError:
        raise NotFound(f"element not found: {element_id}") from None
    result = _serialize(el)
    relations, truncated = _relations(el)
    result["relations"] = relations
    if truncated:
        result["relations_truncated"] = truncated
    return result
