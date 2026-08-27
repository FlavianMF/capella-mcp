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

_LAYER_ATTRS = {
    "oa": "oa",
    "sa": "sa",
    "la": "la",
    "pa": "pa",
    "epbs": "epbs",
}


class NotFound(Exception):
    """The requested layer/element genuinely does not exist in the model."""


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
        "type": type(el).__name__,
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

    elements = model.search(type_filter, below=layer_obj)
    return {"elements": [_serialize(el) for el in elements]}


def get_element(abs_path: Path, element_id: str) -> dict:
    model = _open(abs_path)
    try:
        el = model.by_uuid(element_id)
    except KeyError:
        raise NotFound(f"element not found: {element_id}") from None
    return _serialize(el)
