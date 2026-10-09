"""Delete mechanisms and test cases for the delete_element phase 1 spike.

Class/bundle names below are best guesses from Capella 7 / Sirius / EMF
sources, NOT verified against a running Capella (none was available when
this kit was written). The driver checks them against discover.py's scan
of the real install first and auto-corrects a bundle/package when a class
with the same simple name exists elsewhere (``resolve_candidates``). To
try other entry points without editing code, pass
``--candidates my.json`` with the same shape as ``MECHANISMS``.

Mechanism fields (consumed by probe.py's Capella-side template):

- ``kind``: ``capella_command`` (org.polarsys.capella.common.ef ICommand,
  constructed with an ExecutionManager), ``emf_command`` (an EMF Command,
  constructed or created from the editing domain), ``static_call`` (a
  static method taking the element (+ session)), ``ecoreutil`` (raw
  EcoreUtil.delete, negative control).
- ``in_transaction``: run inside python4capella's
  ``model.start_transaction()``/``commit_transaction()`` (what PRD R5
  wants). False = let the command open its own (ExecutionManager /
  command stack).
- ``bool_rules``: how to set the constructor's boolean parameters, by
  substring of the field each boolean lands in (discovered at run time by
  constructing throwaway instances and diffing their boolean fields).
  Unmatched booleans are False.
"""

from __future__ import annotations

import copy

EXECUTION_MANAGER_LOOKUPS = [
    # (bundle, class, static method) -- called with the element.
    ("org.polarsys.capella.common.helpers", "org.polarsys.capella.common.helpers.TransactionHelper", "getExecutionManager"),
    # fallback: ExecutionManagerRegistry.getInstance().getExecutionManager(editingDomain)
    ("org.polarsys.capella.common.ef", "org.polarsys.capella.common.ef.ExecutionManagerRegistry", "getInstance"),
]

MECHANISMS: dict[str, dict] = {
    "capella_cmd_in_tx": {
        "description": "CapellaDeleteCommand (the GUI's Delete), confirmation/steps off, ensureTransaction off, "
                       "run() inside python4capella's transaction -- the PRD R5 target shape",
        "kind": "capella_command",
        "bundle": "org.polarsys.capella.core.platform.sirius.ui.commands",
        "class": "org.polarsys.capella.core.platform.sirius.ui.commands.CapellaDeleteCommand",
        "in_transaction": True,
        "invoke": "run",
        "bool_rules": {"confirm": False, "step": False, "transaction": False, "prompt": False},
        "control": False,
    },
    "capella_cmd_em": {
        "description": "CapellaDeleteCommand, confirmation/steps off, executed through its ExecutionManager "
                       "(own transaction, no python4capella transaction) -- fallback shape",
        "kind": "capella_command",
        "bundle": "org.polarsys.capella.core.platform.sirius.ui.commands",
        "class": "org.polarsys.capella.core.platform.sirius.ui.commands.CapellaDeleteCommand",
        "in_transaction": False,
        "invoke": "em_execute",
        "bool_rules": {"confirm": False, "step": False, "transaction": True, "prompt": False},
        "control": False,
    },
    "delete_structure_in_tx": {
        "description": "DeleteStructureCommand (non-UI semantic delete in core.model.handler, which the GUI "
                       "command is believed to delegate to), canExecute()+execute() inside the transaction",
        "kind": "emf_command",
        "bundle": "org.polarsys.capella.core.model.handler",
        "class": "org.polarsys.capella.core.model.handler.command.DeleteStructureCommand",
        "in_transaction": True,
        "invoke": "execute",
        "bool_rules": {"confirm": False, "step": False, "prompt": False},
        "control": False,
    },
    "emf_delete_command_in_tx": {
        "description": "EMF Edit DeleteCommand.create(domain, [element]): removes the element and every "
                       "cross-reference the domain knows of, no Capella rules (control)",
        "kind": "emf_command",
        "bundle": "org.eclipse.emf.edit",
        "class": "org.eclipse.emf.edit.command.DeleteCommand",
        "factory": "create",
        "in_transaction": True,
        "invoke": "execute",
        "bool_rules": {},
        "control": True,
    },
    "sirius_util_delete_in_tx": {
        "description": "Sirius SiriusUtil.delete(element, session): delete + session cross-referencer "
                       "cleanup, no Capella rules (control)",
        "kind": "static_call",
        "bundle": "org.eclipse.sirius",
        "class": "org.eclipse.sirius.business.api.helper.SiriusUtil",
        "method": "delete",
        "in_transaction": True,
        "bool_rules": {},
        "control": True,
    },
    "ecoreutil_delete": {
        "description": "EcoreUtil.delete(element, true) -- what python4capella's delete() wraps; forbidden "
                       "by R5, negative control to show what dangling looks like",
        "kind": "ecoreutil",
        "bundle": "org.eclipse.emf.ecore",
        "class": "org.eclipse.emf.ecore.util.EcoreUtil",
        "in_transaction": True,
        "bool_rules": {},
        "control": True,
    },
}

# Target of the seeded interaction: "Fornecer velocidade do veículo".
FE_TARGET_ID = "d8ec91aa-619b-46e1-897c-fa62a90347c9"

# Element ids are the car_hmi fixture's (tests/fixtures/car_hmi), stable
# across copies. "seed:<key>" ids are filled from the seed step's result.
CASES: dict[str, dict] = {
    "oa_leaf_in_oabd": {
        "label": "Monitorar velocidade do veículo",
        "type": "OperationalActivity",
        "element_id": "b2fd6238-ce55-4bdd-af1a-3779d1570bd2",
        "why": "the issue #4 shape: leaf activity shown in the OABD 'Atividades de Velocidade do Veículo'; "
               "seeded: source of the spike interaction, involved in the capability, allocated to Motorista, "
               "shown in the seeded OAIB, root of a seeded OABD",
    },
    "oa_with_children": {
        "label": "Exibir velocidade do veículo",
        "type": "OperationalActivity",
        "element_id": "870edb15-7810-4f4c-a240-256b7e95b959",
        "why": "owns 2 activities and the seeded interaction (cascade case); root of the committed OABD, "
               "which R9 expects Capella to delete",
    },
    "entity_with_child": {
        "label": "Veículo",
        "type": "OperationalEntity",
        "element_id": "54f2ed21-b371-4965-b7de-49a8a836d158",
        "why": "owns 'Painel de Instrumentos' (cascade case); seeded: involved in the capability, shown in "
               "the seeded OEB/OCB",
    },
    "actor": {
        "label": "Motorista",
        "type": "OperationalActor",
        "element_id": "ecf92423-5bda-4eac-b9a8-ecc915d293f0",
        "why": "leaf actor; seeded: involved in the capability, allocates the leaf activity",
    },
    "capability": {
        "label": "Visualizar velocidade do veículo",
        "type": "OperationalCapability",
        "element_id": "2780cacc-c43d-40ba-8fb0-1696dd7906ff",
        "why": "seeded: owns entity and activity involvements, shown in the seeded OCB",
    },
    "functional_exchange": {
        "label": "Spike Interaction",
        "type": "FunctionalExchange",
        "element_id": "seed:functional_exchange",
        "why": "seeded OA interaction Monitorar -> Fornecer (owned by Exibir), an edge in the seeded OAIB; "
               "check its ports go too",
    },
    "root_activity": {
        "label": "Root Operational Activity",
        "type": "OperationalActivity",
        "element_id": "2898e9d8-513f-484c-9fae-2d9fa4c42e8b",
        "why": "R2 protected root: observe whether Capella itself refuses (the server will refuse anyway)",
    },
}

# Default plan: the candidate production mechanisms on every case, the
# controls only on the issue #4 case (they are there to show the
# difference, not to be characterised in full).
PRIMARY = ["capella_cmd_in_tx", "capella_cmd_em", "delete_structure_in_tx"]
CONTROLS = ["emf_delete_command_in_tx", "sirius_util_delete_in_tx", "ecoreutil_delete"]
CONTROL_CASES = ["oa_leaf_in_oabd"]

QUICK_PLAN = [("capella_cmd_in_tx", "oa_leaf_in_oabd"), ("ecoreutil_delete", "oa_leaf_in_oabd")]


def default_plan(mechanisms: list[str] | None = None, cases: list[str] | None = None) -> list[tuple[str, str]]:
    """(mechanism, case) pairs to run. Explicit lists give their full
    cross product; otherwise primaries x all cases + controls x CONTROL_CASES."""
    if mechanisms or cases:
        ms = mechanisms or list(MECHANISMS)
        cs = cases or list(CASES)
        return [(m, c) for m in ms for c in cs]
    plan = [(m, c) for m in PRIMARY for c in CASES]
    plan += [(m, c) for m in CONTROLS for c in CONTROL_CASES]
    return plan


def resolve_candidates(mechanisms: dict[str, dict], discovered: list[dict] | None) -> tuple[dict[str, dict], list[str]]:
    """Check each mechanism's (bundle, class) against discover.py output.

    Returns a corrected deep copy plus human-readable notes. When the
    exact class is missing but one or more classes share its simple name,
    the first match (preferring the same bundle) replaces it. With no
    discovery data (``None``), mechanisms are returned unchanged."""
    out = copy.deepcopy(mechanisms)
    notes: list[str] = []
    if discovered is None:
        return out, ["no discovery data: candidate classes used as written"]
    exact = {(d["bundle"], d["class"]) for d in discovered}
    by_class = {d["class"]: d for d in discovered}
    by_simple: dict[str, list[dict]] = {}
    for d in discovered:
        by_simple.setdefault(d["class"].rsplit(".", 1)[-1], []).append(d)
    for name, mech in out.items():
        if mech["kind"] == "ecoreutil":
            continue  # EcoreUtil itself doesn't match the Delete pattern
        key = (mech["bundle"], mech["class"])
        if key in exact:
            mech["discovery"] = "found"
            continue
        if mech["class"] in by_class:
            hit = by_class[mech["class"]]
            notes.append(f"{name}: {mech['class']} lives in bundle {hit['bundle']}, not {mech['bundle']}")
            mech["bundle"] = hit["bundle"]
            mech["discovery"] = "bundle_corrected"
            continue
        simple = mech["class"].rsplit(".", 1)[-1]
        hits = by_simple.get(simple, [])
        # Static helpers (SiriusUtil) don't match the Delete pattern; only
        # report what we can actually judge.
        if not hits:
            mech["discovery"] = "not_found" if "Delet" in simple else "not_scanned"
            if mech["discovery"] == "not_found":
                notes.append(f"{name}: no class named {simple} in the scanned bundles")
            continue
        hits = sorted(hits, key=lambda d: d["bundle"] != mech["bundle"])
        notes.append(f"{name}: {mech['class']} not found; using {hits[0]['class']} from {hits[0]['bundle']}")
        mech["bundle"], mech["class"] = hits[0]["bundle"], hits[0]["class"]
        mech["discovery"] = "class_corrected"
    return out, notes
