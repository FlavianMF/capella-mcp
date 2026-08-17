"""Headless bridge to Capella via python4capella.

Every call is a full, isolated Capella headless process: open model -> do
one operation -> (save if writing) -> exit. No state is kept between calls.
See docs/decisions/0002-headless-por-chamada.md and
docs/concepts/python4capella-api.md for the rationale and the underlying
API these scripts are generated against.

Script bodies below are only reachable through the fixed templates in this
module -- tool arguments are always interpolated via repr()/dict lookups,
never through eval() or string concatenation into executable code, so a
malicious `type_filter`/`attributes` value can at worst cause a caught
exception, not arbitrary code execution inside the Capella process.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import textwrap
import traceback
import uuid
import zipfile
from pathlib import Path

CAPELLA_BIN = os.environ.get("CAPELLA_BIN", "/opt/capella/capella")
MODELS_ROOT = Path(os.environ.get("CAPELLA_MODELS_ROOT", "/workspace/models")).resolve()
WORKSPACE_ROOT = Path(os.environ.get("CAPELLA_WORKSPACE_ROOT", "/tmp/capella-mcp-workspaces"))
DEFAULT_TIMEOUT = float(os.environ.get("CAPELLA_TIMEOUT_SECONDS", "180"))

# EASE's `workspace://` URL handler (used by include() and by the -data
# script argument itself) requires a real Eclipse *project* -- a loose file
# at the workspace root ("workspace:/script.py") fails with
# "Path must include project and resource name". So every headless call
# needs: (1) our generated script inside its own throwaway project, and
# (2) the python4capella "Python4Capella" project (which ships the
# simplified_api our preamble include()s) imported into that same fresh
# workspace -- it does NOT auto-populate a brand new -data dir on its own,
# it's normally only there because someone opened the Capella GUI once.
# Both are registered via the commandline core's `-import "path|path"`.
_SCRIPT_PROJECT_NAME = "mcp_script"

_PROJECT_DESCRIPTOR = """<?xml version="1.0" encoding="UTF-8"?>
<projectDescription>
\t<name>{name}</name>
\t<comment></comment>
\t<projects>
\t</projects>
\t<buildSpec>
\t</buildSpec>
\t<natures>
\t</natures>
</projectDescription>
"""

_python4capella_project_dir: Path | None = None


def _ensure_python4capella_project() -> Path:
    """Return a directory holding the Eclipse project for python4capella's
    simplified_api, extracting it from the installed plugin jar on first
    use and caching it under WORKSPACE_ROOT for subsequent calls."""
    global _python4capella_project_dir
    if _python4capella_project_dir is not None and (_python4capella_project_dir / ".project").exists():
        return _python4capella_project_dir

    target = WORKSPACE_ROOT / "_python4capella_project" / "Python4Capella"
    if (target / ".project").exists():
        _python4capella_project_dir = target
        return target

    capella_home = Path(CAPELLA_BIN).resolve().parent
    plugin_jars = sorted((capella_home / "plugins").glob("org.eclipse.python4capella_*.jar"))
    if not plugin_jars:
        raise BridgeError(
            f"python4capella plugin jar not found under {capella_home / 'plugins'} "
            "(expected org.eclipse.python4capella_<version>.jar)"
        )

    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(plugin_jars[-1]) as jar, jar.open("zips/Python4Capella.zip") as inner:
        with zipfile.ZipFile(io.BytesIO(inner.read())) as project_zip:
            project_zip.extractall(target)

    _python4capella_project_dir = target
    return target


# CapellaModel().open(path) resolves `path` through the same EASE
# workspace:// mechanism as include() (see CapellaPlatform.getWorkspaceFile
# in the simplified_api) -- it is NOT a raw filesystem path. So MODELS_ROOT
# itself needs to be a registered Eclipse project too, and the string
# handed to open() must be workspace-relative ("models/<model_path>"), not
# the absolute filesystem path resolve_model_path() returns.
_MODELS_PROJECT_NAME = "models"


def _ensure_models_project() -> Path:
    project_file = MODELS_ROOT / ".project"
    if not project_file.exists():
        MODELS_ROOT.mkdir(parents=True, exist_ok=True)
        project_file.write_text(
            _PROJECT_DESCRIPTOR.format(name=_MODELS_PROJECT_NAME), encoding="utf-8"
        )
    return MODELS_ROOT


def _workspace_path_for_model(abs_path: Path) -> str:
    # CapellaPlatform.getWorkspaceFile(path) builds "workspace:/" + path --
    # a single slash. EASE's workspace:// URL handler only resolves the
    # double-slash form (project name as the URL "host", e.g.
    # workspace://models/demo.aird), so we need a leading "/" here to make
    # the concatenation land on "workspace://..." instead of "workspace:/.".
    return f"/{_MODELS_PROJECT_NAME}/{abs_path.relative_to(MODELS_ROOT).as_posix()}"


LAYER_METHODS = {
    "oa": "get_operational_analysis",
    "sa": "get_system_analysis",
    "la": "get_logical_architecture",
    "pa": "get_physical_architecture",
    "epbs": "get_e_p_b_s_architecture",
}

# (layer, type_name) -> real Sirius representation/mapping names, extracted
# from the .odesign files shipped inside Capella's own
# org.polarsys.capella.core.sirius.analysis plugin jar (see
# docs/second_brain for the extraction method). "Breakdown" diagrams are
# flat NodeMapping trees (never nested containers) with a regular
# <Prefix>_<Type> / <Prefix>_<Type>_sub{Functions,Components} naming
# convention across all 5 layers -- confirmed by reading the raw odesign
# XML, not guessed. pkg_method/owned_root_method resolve root-level
# elements (mirrors LAYER_METHODS' own style); children_method walks one
# level of containment (Function subtypes share get_owned_functions;
# component/configuration-item subtypes each have their own).
BREAKDOWN_DIAGRAMS = {
    ("oa", "OperationalActivity"): {
        "diagram": "Operational Activity Breakdown",
        "node_mapping": "OAB_OperationalActivity",
        "edge_mapping": "OAB_OperationalActivity_subFunctions",
        "pkg_method": "get_operational_activity_pkg",
        "owned_root_method": "get_owned_operational_activities",
        "children_method": "get_owned_functions",
    },
    ("oa", "OperationalEntity"): {
        # Unlike every sibling entry, the edge mapping name has an
        # embedded space and no OEB_ prefix -- confirmed against the real
        # oa.odesign (org.polarsys.capella.core.sirius.analysis jar), not
        # guessed; get_representation_mapping_by_name matches by exact
        # string so the space is fine, just not the usual naming pattern.
        #
        # KNOWN LIMITATION (2026-08-14, not fixed -- upstream headless
        # Sirius gap, not a bug in this module): create_diagram produces
        # correct node/edge data for this type (verified via get_diagram/
        # list_elements), but export_diagram's PNG is always blank for it.
        # Root cause narrowed empirically, not just theorized:
        #   - OEB_OperationalEntities is the only NodeMapping among all 9
        #     BREAKDOWN_DIAGRAMS entries with a non-recursive
        #     semanticCandidatesExpression ("service:getOEBScopeBreakdown()"
        #     vs e.g. LCB_LogicalComponent's
        #     "service:self.getCBComponentSemanticCandidates()") and a
        #     generic domainClass="Component" instead of a concrete class
        #     name -- both unique to this mapping across the 9.
        #   - Its raw notation XML is missing the GMF "compartment" child
        #     node (type="3003") that every other mapping's DNode gets --
        #     present even on other mappings that share its
        #     `conditionnalStyles predicateExpression="aql:self = container"`
        #     block (LCB_LogicalComponent, PCB_PhysicalComponent,
        #     CIBD_ConfigurationItem all have it too and render fine, PNG
        #     confirmed non-blank -- so that predicate alone isn't the
        #     cause).
        #   - Tested and disproven live against tests/fixtures/car_hmi:
        #     (a) manually apply_mapping-ing the root itself as an extra
        #     node -- still type="2001"+"5002" only, no "3003", so it is
        #     not about the root being excluded; (b) hide()+reveal() to
        #     force GMF view recreation -- same result; (c) inherited from
        #     an earlier session, DialectManager.INSTANCE.refresh() --
        #     byte-identical output. The exported PNG for the sole
        #     remaining node has correct bounds but paints nothing at all,
        #     not even the shape's border -- consistent with the
        #     DDiagramElement's style failing to resolve during headless
        #     view creation specifically for this mapping, not a layout or
        #     visibility issue we control from this script layer.
        #   - Fixing this would need attaching a debugger to the headless
        #     JVM to see the actual exception during GMF view-provider
        #     dispatch -- out of scope here; documented so create_diagram
        #     isn't silently "wrong" for OperationalEntity/OperationalActor,
        #     and so nobody re-discovers this from scratch. Only the
        #     rendered image is affected; the diagram and its data are
        #     otherwise fully usable (list_diagrams/get_diagram, and the
        #     model keeps the real containment/edges).
        #
        # FOLLOW-UP (2026-08-15): investigated Capella's/python4capella's own
        # docs + every alternative OA representation for entities/actors, per
        # docs/second_brain (same note as above) and
        # /home/flv/.claude/plans/fa-a-uma-investiga-o-mais-quiet-crescent.md.
        # Three more concrete levers tried live, all still not enough to fix
        # this without a richer, more invasive change than fits this bridge:
        #   (d) org.eclipse.sirius.diagram.business.api.refresh.
        #       CanonicalSynchronizerFactory.INSTANCE.
        #       createCanonicalSynchronizer(gmfDiagram).synchronize() (the
        #       real GMF-notation-level sync, reached via
        #       SiriusGMFHelper.getGmfDiagram(dDiagram) -- distinct from (c)
        #       above, which only operates at the Sirius/DDiagram level) --
        #       ran clean, output still byte-identical, no "3003" child.
        #   (e) the native python4capella call `Sirius.open_representation()`
        #       (backed by org.eclipse.python4capella.modules.SiriusModule,
        #       never called anywhere in capella.py/diagram.py or exposed by
        #       the Diagram wrapper) -- meant to simulate "open the editor",
        #       which is what normally triggers full GMF materialization
        #       interactively. Ran clean, returned None, no effect -- headless
        #       RCP has no real workbench window/PartService for it to open
        #       an editor into, so it's plausibly a silent no-op here.
        #   (f) switching this entry to the "Operational Entity Blank"
        #       representation instead (ContainerMapping "OAB_Entity1",
        #       domainClass="Entity", covers both entity and actor creation
        #       tools -- confirmed via oa.odesign) -- structurally the same
        #       node technology (ContainerMapping/FlatContainerStyleDescription)
        #       as the 8 working types, and *not* synchronized at all
        #       (semanticCandidatesExpression="", createElements="false"),
        #       so no auto-populate/duplicate-node risk either. This one
        #       genuinely progresses further: the exported PNG's own pixel
        #       dimensions correctly matched the bounds we set (e.g. 240x140
        #       for a [100,100,220,120] box, vs OEB's degenerate ~30x60 for
        #       real content), proving the GMF geometry *is* real this time.
        #       But the rendered content itself is still not usable --
        #       zoomed inspection showed only a faint drop-shadow (3 grey
        #       tones, no fill, no border, no icon, no label text), i.e. the
        #       container's actual Figure never paints, same *family* of gap
        #       as the NodeMapping case, just one layer further along. Not
        #       adopted -- would trade one non-working representation for a
        #       differently-non-working one, and the user's own explicit call
        #       was to replace OEBD with this only if it actually worked.
        # Conclusion after 6 independent disproven fixes across 2 different
        # Sirius node technologies (NodeMapping and ContainerMapping): this
        # is a genuine headless Sirius/GMF limitation, not something fixable
        # from this bridge's abstraction level (fixed EASE script templates
        # over python4capella's simplified_api) without dropping to raw
        # Draw2D/GEF figure-refresh internals well beyond what's documented
        # or exercised anywhere in python4capella itself.
        "diagram": "Operational Entity Breakdown",
        "node_mapping": "OEB_OperationalEntities",
        "edge_mapping": "containedIn Mapping",
        "pkg_method": "get_entity_pkg",
        "owned_root_method": "get_owned_entities",
        "children_method": "get_owned_entities",
    },
    ("oa", "OperationalActor"): {
        # Same diagram/mapping as OperationalEntity above -- both are
        # Python wrapper classes over the same underlying EMF "Entity"
        # class (distinguished only by an actor flag), and create_diagram
        # infers type_name from a root_id via type(root).__name__, which
        # can resolve to either wrapper depending on which one the found
        # element actually is.
        "diagram": "Operational Entity Breakdown",
        "node_mapping": "OEB_OperationalEntities",
        "edge_mapping": "containedIn Mapping",
        "pkg_method": "get_entity_pkg",
        "owned_root_method": "get_owned_entities",
        "children_method": "get_owned_entities",
    },
    ("sa", "SystemFunction"): {
        "diagram": "System Function Breakdown",
        "node_mapping": "SFB_SystemFunction",
        "edge_mapping": "SFB_SystemFunction_subFunctions",
        "pkg_method": "get_system_function_pkg",
        "owned_root_method": "get_owned_system_functions",
        "children_method": "get_owned_functions",
    },
    ("la", "LogicalFunction"): {
        "diagram": "Logical Function Breakdown",
        "node_mapping": "LFB_LogicalFunction",
        "edge_mapping": "LFB_LogicalFunction_subFunctions",
        "pkg_method": "get_logical_function_pkg",
        "owned_root_method": "get_owned_logical_functions",
        "children_method": "get_owned_functions",
    },
    ("la", "LogicalComponent"): {
        "diagram": "Logical Component Breakdown",
        "node_mapping": "LCB_LogicalComponent",
        "edge_mapping": "LCB_LogicalComponent_subComponents",
        "pkg_method": "get_logical_component_pkg",
        "owned_root_method": "get_owned_logical_components",
        "children_method": "get_owned_logical_components",
    },
    ("pa", "PhysicalFunction"): {
        "diagram": "Physical Function Breakdown",
        "node_mapping": "PFB_PhysicalFunction",
        "edge_mapping": "PFB_PhysicalFunction_subFunctions",
        "pkg_method": "get_physical_function_pkg",
        "owned_root_method": "get_owned_physical_functions",
        "children_method": "get_owned_functions",
    },
    ("pa", "PhysicalComponent"): {
        "diagram": "Physical Component Breakdown",
        "node_mapping": "PCB_PhysicalComponent",
        "edge_mapping": "PCB_PhysicalComponent_subComponents",
        "pkg_method": "get_physical_component_pkg",
        "owned_root_method": "get_owned_physical_components",
        "children_method": "get_owned_physical_components",
    },
    ("epbs", "ConfigurationItem"): {
        "diagram": "Configuration Items Breakdown",
        "node_mapping": "CIBD_ConfigurationItem",
        "edge_mapping": "CIBD_ConfigurationItem_subComponents",
        "pkg_method": "get_configuration_item_pkg",
        "owned_root_method": "get_owned_configuration_items",
        "children_method": "get_owned_configuration_items",
    },
    # Mode State Machine (common.odesign, shared across layers -- only "la"
    # wired here since LogicalComponent is this bridge's most complete
    # type). Structurally a perfect fit for the same breakdown algorithm
    # (single root, one-level flat children, NodeMapping) EVEN THOUGH it
    # isn't from oa.odesign's "Blank"/ContainerMapping family -- confirmed
    # live (2026-08-15) that MSM_ModeState (a NodeMapping, states are never
    # nested here since composite/nested states aren't supported by
    # create_element) paints correctly in headless Sirius, unlike every
    # ContainerMapping-based diagram (see CONTAINER_DIAGRAMS' comment) --
    # exported PNG showed a real rendered state box (icon, label, rounded
    # shape), not a blank fragment.
    #
    # No package-level root exists for a Region (it's nested 3 levels deep
    # inside an arbitrary Component's arbitrary StateMachine) -- root_id is
    # always required. pkg_method is deliberately a self-documenting
    # nonexistent attribute name so the AttributeError from a type_name-only
    # call (skipping root_id) explains why, instead of a generic failure.
    ("la", "Region"): {
        "diagram": "Mode State Machine",
        "node_mapping": "MSM_ModeState",
        "edge_mapping": "MSM_Transition",
        "pkg_method": "root_id_required_for_region_no_package_level_discovery",
        "owned_root_method": "root_id_required_for_region_no_package_level_discovery",
        "children_method": "get_owned_states",
    },
}

# CONTAINER_DIAGRAMS: the "Blank" family of OA representations (Operational
# Entity Blank, Operational Activity Interaction Blank, etc.) use Sirius
# ContainerMapping instead of NodeMapping.
#
# RESOLVED (2026-08-15): every ContainerMapping-produced DNodeContainer
# used to export as a blank PNG headless -- confirmed universal across
# unrelated domains (Entity via OAB_Entity1, OperationalActivity via OAIB's
# container mapping), so not an Entity-domain quirk. Root-caused via a live
# JDWP debugger attached to the headless JVM (breakpoint on
# org.eclipse.gmf.runtime.notation.impl.NodeImpl.<init>, filtered out the
# ~100 hits from the model file's own XML parsing on open): python4capella's
# apply_mapping() (simplified_api/diagram.py) creates the DNodeContainer via
# raw EMF instantiation and never populates ownedStyle (only happens if a
# `customizations` dict is passed, which no call site in this module does)
# -- it never routes through Sirius's real element-creation service. The
# GMF view model itself (container AND its label view, via
# DNodeContainerNameViewFactory) turned out to materialize correctly and
# completely even so, headless, no workbench required -- ruling out every
# "EditPart/canonical-sync doesn't run headless" theory. The actual gap was
# narrower than that: just the wrong creation call.
#
# THE FIX: org.polarsys.capella.core.sirius.analysis.DiagramServices
# .getDiagramServices().createContainer(mapping, modelElement, container,
# diagram) is Capella's own public service method -- the same one its
# interactive UI calls -- and routes through the real
# DDiagramSynchronizer/DDiagramElementSynchronizer.createNewNode() pipeline.
# Verified live against two cases (flat container, nested parent/child):
# both rendered with a real border, fill, icon, and full label text,
# byte-size in the same range as always-working NodeMapping diagrams. No
# patch needed to python4capella or Capella -- DiagramServices is already
# public and already on the classpath every headless call loads. NodeMapping
# and EdgeMapping creation elsewhere in this module still use apply_mapping()
# since those were never observed broken (NodeMapping's own canonical-sync
# auto-populates and fully materializes regardless of this gap).
#
# create_container_diagram() below implements this technology for:
#   - ("oa", "OperationalEntity")/("oa", "OperationalActor") -> "Operational
#     Entity Blank" (mapping OAB_Entity1), reusing the exact same
#     pkg_method/owned_root_method/children_method shape as
#     BREAKDOWN_DIAGRAMS's OperationalEntity/OperationalActor entries since
#     the underlying semantic structure (EntityPkg.get_owned_entities(),
#     recursive) is identical -- only the Sirius mapping technology differs.
#   - ("oa", "OperationalActivity") -> "Operational Activity Interaction
#     Blank" (container mapping "OAIB Operational Activity", edge mapping
#     "OAIB Interaction"): same pkg_method/owned_root_method/children_method
#     as BREAKDOWN_DIAGRAMS's OperationalActivity entry (get_operational_
#     activity_pkg/get_owned_operational_activities/get_owned_functions),
#     since OAIB's containers are just activities again -- but this one also
#     has an edge_mapping, so create_container_diagram optionally collects
#     cross-activity functional-exchange relations (same logic
#     create_diagram's include_relations already uses) and wires them as
#     edges between the activity containers (still via apply_mapping(), edges
#     were never part of the broken-paint gap).
#
# Operational Capabilities Blank (OCB) and Class Diagram Blank (CDB) are
# implemented too, but as their own dedicated functions
# (create_capability_diagram, create_class_diagram) since neither fits this
# dict's single-container-mapping tree shape -- see their own comments/
# docstrings. Both use the same DiagramServices.createContainer() fix.
# Mode State Machine (MSM/"State Machine") is a BREAKDOWN_DIAGRAMS entry
# instead (NodeMapping, never affected by this bug in the first place).
#
# Still blocked, unrelated to this fix: Operational Role Blank (ORB) --
# python4capella's simplified_api/capella.py has NO wrapper class for
# OperationalRole at all (confirmed by grep across the whole source tree),
# so there is no semantic element to target any creation call with in the
# first place. Would need the underlying addon extended, out of reach from
# this bridge.
#
# Sequence/scenario diagrams (Operational Interaction Scenario/"OES",
# Activity Interaction Scenario/"OAS") -- RESOLVED (2026-08-17) after two
# earlier rounds concluded "blocked, no found public entry point". See the
# comment above _SCENARIO_DIAGRAM_MAPPINGS/create_scenario_diagram() further
# down this module for the full root-cause investigation and fix (both the
# export-time Sirius internal NPE and the semantic-model construction).
CONTAINER_DIAGRAMS = {
    ("oa", "OperationalEntity"): {
        "diagram": "Operational Entity Blank",
        "container_mapping": "OAB_Entity1",
        "pkg_method": "get_entity_pkg",
        "owned_root_method": "get_owned_entities",
        "children_method": "get_owned_entities",
    },
    ("oa", "OperationalActor"): {
        "diagram": "Operational Entity Blank",
        "container_mapping": "OAB_Entity1",
        "pkg_method": "get_entity_pkg",
        "owned_root_method": "get_owned_entities",
        "children_method": "get_owned_entities",
    },
    ("oa", "OperationalActivity"): {
        "diagram": "Operational Activity Interaction Blank",
        "container_mapping": "OAIB Operational Activity",
        "edge_mapping": "OAIB Interaction",
        "pkg_method": "get_operational_activity_pkg",
        "owned_root_method": "get_owned_operational_activities",
        "children_method": "get_owned_functions",
    },
}

# Sizing/spacing constants for the layered-tree layout computed in
# _layout_tree -- see docs/second_brain and the upgrade plan for the
# formulas' rationale (no auto-layout exists anywhere in python4capella,
# this is our own deterministic algorithm).
_CHAR_WIDTH_PX = 7
_PADDING_X = 16
_MIN_WIDTH = 80
_MAX_WIDTH = 240
_NODE_HEIGHT = 40
_HORIZONTAL_GAP = 20
_VERTICAL_GAP = 60


def _node_width(label: str) -> int:
    return max(_MIN_WIDTH, min(_MAX_WIDTH, len(label) * _CHAR_WIDTH_PX + _PADDING_X))


def _layout_tree(tree: list[dict]) -> dict[str, list[int]]:
    """Deterministic layered-tree layout (bottom-up subtree width, top-down
    placement). `tree` is a flat list of {"id", "label", "parent_id"}
    (parent_id=None for roots). Returns {id: [x, y, width, height]}."""
    children: dict[str | None, list[dict]] = {}
    by_id = {}
    for node in tree:
        by_id[node["id"]] = node
        children.setdefault(node["parent_id"], []).append(node)

    widths: dict[str, int] = {}
    subtree_widths: dict[str, int] = {}
    depths: dict[str, int] = {}

    def compute_subtree_width(node: dict, depth: int) -> int:
        depths[node["id"]] = depth
        widths[node["id"]] = _node_width(node["label"])
        kids = children.get(node["id"], [])
        if not kids:
            subtree_widths[node["id"]] = widths[node["id"]]
        else:
            kids_total = sum(compute_subtree_width(k, depth + 1) for k in kids)
            kids_total += _HORIZONTAL_GAP * (len(kids) - 1)
            subtree_widths[node["id"]] = max(widths[node["id"]], kids_total)
        return subtree_widths[node["id"]]

    roots = children.get(None, [])
    for root in roots:
        compute_subtree_width(root, 0)

    bounds: dict[str, list[int]] = {}

    def place(node: dict, x0: int):
        node_id = node["id"]
        w = widths[node_id]
        sw = subtree_widths[node_id]
        x = x0 + (sw - w) // 2
        y = depths[node_id] * (_NODE_HEIGHT + _VERTICAL_GAP)
        bounds[node_id] = [x, y, w, _NODE_HEIGHT]
        cursor = x0
        for kid in children.get(node_id, []):
            place(kid, cursor)
            cursor += subtree_widths[kid["id"]] + _HORIZONTAL_GAP

    cursor = 0
    for root in roots:
        place(root, cursor)
        cursor += subtree_widths[root["id"]] + _HORIZONTAL_GAP * 2

    return bounds


class BridgeError(Exception):
    """Raised for anything that stops a tool call from producing a result:
    invalid model_path, Capella process failure/timeout, or an error the
    generated script itself caught and reported."""


def resolve_model_path(model_path: str) -> Path:
    """Resolve a tool-supplied model_path against MODELS_ROOT, rejecting escapes."""
    resolved = (MODELS_ROOT / model_path).resolve()
    if resolved != MODELS_ROOT and MODELS_ROOT not in resolved.parents:
        raise BridgeError(f"model_path escapes models root: {model_path!r}")
    if not resolved.exists():
        raise BridgeError(f"model not found: {model_path!r}")
    return resolved


def _preamble(result_path: Path) -> str:
    return textwrap.dedent(f"""\
        include('workspace://Python4Capella/simplified_api/capella.py')
        import json, traceback

        def _write_result(data):
            with open({str(result_path)!r}, "w") as f:
                json.dump(data, f)

        def _element_id(el):
            # NOTE: best-effort — Capella's metamodel (CapellaElement) carries a
            # persistent XMI `id`, exposed on the raw Java object. Not yet
            # validated against a running Capella instance (needs Fase 2 image +
            # a real fixture model). If this getter is wrong, get_element/
            # update_element will surface a clear BridgeError instead of
            # silently returning wrong data.
            try:
                return el.get_java_object().getId()
            except Exception:
                return None

        def _serialize(el):
            return {{
                "id": _element_id(el),
                "label": el.get_label() if hasattr(el, "get_label") else None,
                "type": type(el).__name__,
            }}
        """)


def _spawn_and_wait(
    cmd: list[str], capture_output: bool = True, text: bool = True, timeout: float | None = None
) -> subprocess.CompletedProcess:
    """subprocess.run()-alike, kept as its own seam (rather than calling
    subprocess.run directly) so both call sites and the unit tests can mock
    it uniformly.

    xvfb-run is a shell wrapper that forks Xvfb and execs capella as
    children of its own process -- subprocess.run(timeout=...)'s implicit
    process.kill() on timeout only SIGKILLs that top-level shell, leaving
    the whole Xvfb+Capella(JVM) tree running forever. Put the child in its
    own process group (start_new_session=True) and, on timeout, kill the
    whole group so a timed-out call doesn't leak a Capella process.
    """
    proc_handle = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc_handle.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc_handle.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc_handle.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc_handle.returncode, stdout, stderr)


def _run_script(script_body: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    python4capella_project = _ensure_python4capella_project()
    models_project = _ensure_models_project()

    call_dir = WORKSPACE_ROOT / uuid.uuid4().hex
    project_dir = call_dir / _SCRIPT_PROJECT_NAME
    project_dir.mkdir(parents=True)
    (project_dir / ".project").write_text(
        _PROJECT_DESCRIPTOR.format(name=_SCRIPT_PROJECT_NAME), encoding="utf-8"
    )
    script_path = project_dir / "script.py"
    result_path = call_dir / "result.json"
    script_path.write_text(_preamble(result_path) + script_body, encoding="utf-8")

    cmd = [
        "xvfb-run", "-a", CAPELLA_BIN,
        "-nosplash", "-consolelog",
        "-application", "org.polarsys.capella.core.commandline.core",
        "-appid", "org.eclipse.python4capella.commandline",
        "-data", str(call_dir),
        "-import", f"{project_dir}|{python4capella_project}|{models_project}",
        f"workspace:/{project_dir.name}/{script_path.name}",
    ]
    try:
        proc = _spawn_and_wait(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"Capella headless call timed out after {timeout}s") from exc

    if not result_path.exists():
        raise BridgeError(
            "Capella headless call produced no result "
            f"(exit={proc.returncode}). stderr tail: {proc.stderr[-2000:]}"
        )

    data = json.loads(result_path.read_text(encoding="utf-8"))
    if "error" in data:
        raise BridgeError(f"{data['error']}\n{data.get('traceback', '')}")
    return data


def list_layers(model_path: str) -> dict:
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layers = []
            for key, method_name in {LAYER_METHODS!r}.items():
                layer = getattr(se, method_name)()
                layers.append({{"layer": key, "present": layer is not None}})
            _write_result({{"layers": layers}})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def list_elements(model_path: str, layer: str, type_filter: str | None = None) -> dict:
    if layer not in LAYER_METHODS:
        raise BridgeError(f"unknown layer {layer!r}, expected one of {sorted(LAYER_METHODS)}")
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    method_name = LAYER_METHODS[layer]
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layer_obj = getattr(se, {method_name!r})()
            type_filter = {type_filter!r}
            if type_filter:
                type_cls = globals().get(type_filter)
                if type_cls is None:
                    raise NameError(f"unknown Capella element type: {{type_filter}}")
                elements = layer_obj.get_all_contents_by_type(type_cls)
            else:
                elements = layer_obj.get_contents()
            _write_result({{"elements": [_serialize(el) for el in elements]}})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def get_element(model_path: str, element_id: str) -> dict:
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            target_id = {element_id!r}
            found = None
            se = model.get_system_engineering()
            for method_name in {list(LAYER_METHODS.values())!r}:
                layer = getattr(se, method_name)()
                if layer is None:
                    continue
                for el in layer.get_all_contents() if hasattr(layer, "get_all_contents") else []:
                    if _element_id(el) == target_id:
                        found = el
                        break
                if found is not None:
                    break
            if found is None:
                _write_result({{"error": f"element not found: {{target_id}}"}})
            else:
                _write_result(_serialize(found))
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def create_element(
    model_path: str,
    layer: str,
    type_name: str,
    name: str,
    parent_id: str | None = None,
    attributes: dict | None = None,
) -> dict:
    if layer not in LAYER_METHODS:
        raise BridgeError(f"unknown layer {layer!r}, expected one of {sorted(LAYER_METHODS)}")
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    method_name = LAYER_METHODS[layer]
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layer_obj = getattr(se, {method_name!r})()

            cls = globals().get({type_name!r})
            if cls is None:
                raise NameError(f"unknown Capella element type: {type_name!r}")

            model.start_transaction()
            try:
                el = cls()
                # NOTE: best-effort attribute wiring, unverified against a real
                # model (needs Fase 2 image + fixture). `name` covers the one
                # attribute virtually every Capella element (NamedElement) has;
                # `attributes` beyond that use a set_<key> convention mirrored
                # from the observed get_<key> convention in the simplified API.
                if hasattr(el, "set_name"):
                    el.set_name({name!r})
                for key, value in ({attributes!r} or {{}}).items():
                    setter = getattr(el, f"set_{{key}}", None)
                    if setter is not None:
                        setter(value)
                parent_id = {parent_id!r}
                if parent_id is not None:
                    container = None
                    for candidate in layer_obj.get_all_contents() if hasattr(layer_obj, "get_all_contents") else []:
                        if _element_id(candidate) == parent_id:
                            container = candidate
                            break
                    if container is None:
                        raise ValueError(f"parent_id not found in layer: {{parent_id}}")
                else:
                    container = layer_obj

                # NOTE: get_contents() returns a disconnected Python-side
                # snapshot of eContents() (see capella.py), not a live EMF
                # collection -- appending to it never actually attaches the
                # element to the model tree. The real containment
                # collections are per-type ("owned_<...>", a live EList
                # supporting .add()), e.g. a LogicalComponent lives under
                # LogicalComponentPkg.get_owned_logical_components() (root)
                # or another LogicalComponent's own
                # get_owned_logical_components() (nesting). LogicalComponent,
                # SystemFunction, LogicalFunction, OperationalActivity,
                # OperationalActor, OperationalEntity, OperationalCapability,
                # StateMachine, Region, State, Mode, DataPkg, Class,
                # FunctionalExchange, Scenario, InstanceRole, and
                # SequenceMessage have all been validated against a real
                # model this way. Other
                # layer/type combinations still need their own container
                # resolved the same way before create_element will actually
                # persist anything for them.
                if {type_name!r} == "LogicalComponent":
                    if hasattr(container, "get_logical_component_pkg"):
                        container = container.get_logical_component_pkg()
                    if not hasattr(container, "get_owned_logical_components"):
                        raise AttributeError(
                            "no get_owned_logical_components() container found "
                            f"on {{type(container).__name__}}"
                        )
                    container.get_owned_logical_components().add(el)
                elif {type_name!r} == "SystemFunction":
                    # SystemAnalysis (root) nests via its SystemFunctionPkg
                    # (get_owned_system_functions); an existing SystemFunction
                    # parent (parent_id case) nests sub-functions directly via
                    # its own get_owned_functions -- different accessor, no
                    # intermediate pkg, mirrored from Function.get_owned_functions()
                    # in simplified_api/capella.py.
                    if hasattr(container, "get_system_function_pkg"):
                        container = container.get_system_function_pkg()
                        container.get_owned_system_functions().add(el)
                    elif hasattr(container, "get_owned_functions"):
                        container.get_owned_functions().add(el)
                    else:
                        raise AttributeError(
                            "no get_system_function_pkg()/get_owned_functions() "
                            f"container found on {{type(container).__name__}}"
                        )
                elif {type_name!r} == "LogicalFunction":
                    # LogicalArchitecture (root) nests via its
                    # LogicalFunctionPkg (get_owned_logical_functions); an
                    # existing LogicalFunction parent (parent_id case) nests
                    # sub-functions directly via its own get_owned_functions
                    # -- same shape as SystemFunction, mirrored from
                    # Function.get_owned_functions() in simplified_api/capella.py.
                    if hasattr(container, "get_logical_function_pkg"):
                        container = container.get_logical_function_pkg()
                        container.get_owned_logical_functions().add(el)
                    elif hasattr(container, "get_owned_functions"):
                        container.get_owned_functions().add(el)
                    else:
                        raise AttributeError(
                            "no get_logical_function_pkg()/get_owned_functions() "
                            f"container found on {{type(container).__name__}}"
                        )
                elif {type_name!r} == "OperationalActivity":
                    # Mirrors SystemFunction/LogicalFunction exactly --
                    # OperationalActivity extends the same Function base
                    # class.
                    if hasattr(container, "get_operational_activity_pkg"):
                        container = container.get_operational_activity_pkg()
                        container.get_owned_operational_activities().add(el)
                    elif hasattr(container, "get_owned_functions"):
                        container.get_owned_functions().add(el)
                    else:
                        raise AttributeError(
                            "no get_operational_activity_pkg()/get_owned_functions() "
                            f"container found on {{type(container).__name__}}"
                        )
                elif {type_name!r} in ("OperationalActor", "OperationalEntity"):
                    # Both wrap the same underlying EMF "Entity" class
                    # (distinguished by an actor flag); EntityPkg exposes
                    # one shared get_owned_entities(). Nesting is only
                    # possible under an OperationalEntity parent -- an
                    # OperationalActor parent has no get_owned_entities()
                    # of its own (actors can't be broken down, by design).
                    if hasattr(container, "get_entity_pkg"):
                        container = container.get_entity_pkg()
                        container.get_owned_entities().add(el)
                    elif hasattr(container, "get_owned_entities"):
                        container.get_owned_entities().add(el)
                    else:
                        raise AttributeError(
                            "no get_entity_pkg()/get_owned_entities() container found "
                            f"on {{type(container).__name__}} (an OperationalActor "
                            "parent cannot own sub-entities)"
                        )
                elif {type_name!r} == "OperationalCapability":
                    # Flat only -- OperationalCapability has no
                    # self-nesting accessor.
                    if {parent_id!r} is not None:
                        raise ValueError(
                            "OperationalCapability has no nesting accessor; omit parent_id"
                        )
                    if not hasattr(container, "get_operational_capability_pkg"):
                        raise AttributeError(
                            "no get_operational_capability_pkg() found on "
                            f"{{type(container).__name__}}"
                        )
                    container.get_operational_capability_pkg().get_owned_operational_capabilities().add(el)
                elif {type_name!r} == "StateMachine":
                    # Owned by a Component (LogicalComponent/PhysicalComponent/
                    # etc, get_owned_state_machines() -- confirmed on
                    # BehavioralComponent, which LogicalComponent extends).
                    # No package-level root -- always requires parent_id.
                    if {parent_id!r} is None:
                        raise ValueError("StateMachine requires parent_id (an existing Component)")
                    if not hasattr(container, "get_owned_state_machines"):
                        raise AttributeError(
                            "no get_owned_state_machines() found on "
                            f"{{type(container).__name__}} (parent_id must be a Component)"
                        )
                    container.get_owned_state_machines().add(el)
                elif {type_name!r} == "Region":
                    # Owned by a StateMachine (or a composite State, not
                    # supported here -- only StateMachine-level regions).
                    if {parent_id!r} is None:
                        raise ValueError("Region requires parent_id (an existing StateMachine)")
                    if not hasattr(container, "get_owned_regions"):
                        raise AttributeError(
                            "no get_owned_regions() found on "
                            f"{{type(container).__name__}} (parent_id must be a StateMachine)"
                        )
                    container.get_owned_regions().add(el)
                elif {type_name!r} in ("State", "Mode"):
                    # Owned by a Region. Composite states (a State owning
                    # its own sub-Regions) are not supported here -- only
                    # flat states directly under a Region.
                    if {parent_id!r} is None:
                        raise ValueError(f"{type_name!r} requires parent_id (an existing Region)")
                    if not hasattr(container, "get_owned_states"):
                        raise AttributeError(
                            "no get_owned_states() found on "
                            f"{{type(container).__name__}} (parent_id must be a Region)"
                        )
                    container.get_owned_states().add(el)
                elif {type_name!r} == "DataPkg":
                    # Every layer already has exactly one default DataPkg
                    # (Component.get_data_pkg(), not a package-of-many like
                    # e.g. LogicalComponentPkg) -- nesting further always
                    # requires parent_id pointing at an existing DataPkg.
                    if {parent_id!r} is None:
                        raise ValueError(
                            "DataPkg requires parent_id (an existing DataPkg -- "
                            "every layer already has a default one)"
                        )
                    if not hasattr(container, "get_owned_data_pkgs"):
                        raise AttributeError(
                            "no get_owned_data_pkgs() found on "
                            f"{{type(container).__name__}} (parent_id must be a DataPkg)"
                        )
                    container.get_owned_data_pkgs().add(el)
                elif {type_name!r} == "Class":
                    if {parent_id!r} is None:
                        raise ValueError("Class requires parent_id (an existing DataPkg)")
                    if not hasattr(container, "get_owned_classes"):
                        raise AttributeError(
                            "no get_owned_classes() found on "
                            f"{{type(container).__name__}} (parent_id must be a DataPkg)"
                        )
                    container.get_owned_classes().add(el)
                elif {type_name!r} == "FunctionalExchange":
                    # Unlike every other type above, an exchange isn't
                    # created "in" a simple container -- it connects two
                    # existing Functions (any Function subtype: Operational
                    # Activity/SystemFunction/LogicalFunction) via a pair of
                    # ports it creates on them, and is owned by their
                    # nearest common ancestor Function. There's no
                    # package-level root, so parent_id here means that
                    # owning Function (reusing the same container-lookup
                    # done above); source_id/target_id (element ids of the
                    # two Functions to connect) travel through `attributes`
                    # since there's no other slot for a second id.
                    # Verified end-to-end against a real model: ports via
                    # Function.get_outputs()/.get_inputs() (real containment,
                    # declared in Activity.ecore -- NOT the FunctionSpecification-
                    # only ownedFunctionPorts some Capella docs mention, which
                    # OperationalActivity/SystemFunction/LogicalFunction don't
                    # have), exchange via FunctionalExchange.set_source_port()/
                    # .set_target_port() (already wrapped in capella.py) and
                    # containment via Function.get_owned_functional_exchanges().
                    if {parent_id!r} is None:
                        raise ValueError(
                            "FunctionalExchange requires parent_id (the owning "
                            "Function -- typically the nearest common ancestor "
                            "of source/target)"
                        )
                    if not hasattr(container, "get_owned_functional_exchanges"):
                        raise AttributeError(
                            "no get_owned_functional_exchanges() found on "
                            f"{{type(container).__name__}} (parent_id must be a Function)"
                        )
                    attrs = {attributes!r} or {{}}
                    source_id = attrs.get("source_id")
                    target_id = attrs.get("target_id")
                    if not source_id or not target_id:
                        raise ValueError(
                            "FunctionalExchange requires attributes={{'source_id': ..., "
                            "'target_id': ...}} (element ids of two existing Functions)"
                        )
                    source_fn = target_fn = None
                    for candidate in layer_obj.get_all_contents() if hasattr(layer_obj, "get_all_contents") else []:
                        cid = _element_id(candidate)
                        if cid == source_id:
                            source_fn = candidate
                        if cid == target_id:
                            target_fn = candidate
                    if source_fn is None:
                        raise ValueError(f"source_id not found in layer: {{source_id}}")
                    if target_fn is None:
                        raise ValueError(f"target_id not found in layer: {{target_id}}")

                    out_port = FunctionOutputPort()
                    out_port.set_name({name!r} + " (out)")
                    source_fn.get_outputs().add(out_port)

                    in_port = FunctionInputPort()
                    in_port.set_name({name!r} + " (in)")
                    target_fn.get_inputs().add(in_port)

                    el.set_source_port(out_port)
                    el.set_target_port(in_port)
                    container.get_owned_functional_exchanges().add(el)
                elif {type_name!r} == "Scenario":
                    # No package-level root -- always owned by an existing
                    # OperationalCapability (get_owned_scenarios(), on
                    # AbstractCapability -- confirmed real in capella.py).
                    if {parent_id!r} is None:
                        raise ValueError("Scenario requires parent_id (an existing OperationalCapability)")
                    if not hasattr(container, "get_owned_scenarios"):
                        raise AttributeError(
                            "no get_owned_scenarios() found on "
                            f"{{type(container).__name__}} (parent_id must be an OperationalCapability)"
                        )
                    container.get_owned_scenarios().add(el)
                elif {type_name!r} == "InstanceRole":
                    # Owned by a Scenario. representedInstance wants an
                    # AbstractInstance (a Part), NOT the Entity/Function
                    # itself -- Entity doesn't implement AbstractInstance
                    # (confirmed: direct setRepresentedInstance(entity) call
                    # raises ClassCastException). Every Entity/Component has
                    # its own self-representing Part, lazily created on
                    # first use via CapellaServices.creationService() and
                    # exposed via getAbstractTypedElements() -- mirrors the
                    # odesign's own InstanceRoleCreationTool operation
                    # (`component.abstractTypedElements->at(1)`). Also,
                    # Py4J can't resolve setRepresentedInstance's overload
                    # (interface-typed parameter vs a concrete arg class --
                    # same class of issue as several other setters in this
                    # module) -- set via raw EMF eSet() instead.
                    if {parent_id!r} is None:
                        raise ValueError("InstanceRole requires parent_id (an existing Scenario)")
                    if not hasattr(container, "get_owned_instance_roles"):
                        raise AttributeError(
                            "no get_owned_instance_roles() found on "
                            f"{{type(container).__name__}} (parent_id must be a Scenario)"
                        )
                    attrs = {attributes!r} or {{}}
                    represented_id = attrs.get("represented_instance_id")
                    if not represented_id:
                        raise ValueError(
                            "InstanceRole requires attributes={{'represented_instance_id': ...}} "
                            "(element id of an existing Entity/Function to represent)"
                        )
                    represented = None
                    for candidate in layer_obj.get_all_contents() if hasattr(layer_obj, "get_all_contents") else []:
                        if _element_id(candidate) == represented_id:
                            represented = candidate
                            break
                    if represented is None:
                        raise ValueError(f"represented_instance_id not found in layer: {{represented_id}}")
                    # AbstractFunction (OperationalActivity/SystemFunction/
                    # LogicalFunction's common ancestor) directly extends
                    # Information.ecore's AbstractInstance -- confirmed in
                    # FunctionalAnalysis.ecore's own eSuperTypes list --
                    # unlike Entity, which doesn't. The odesign's own
                    # InstanceRoleCreationTool operations confirm both
                    # shapes: OES's "component"/"actor" tools set
                    # representedInstance to `component.abstractTypedElements
                    # ->at(1)` (the Part), OAS's "activity" tool sets it
                    # directly to `component` (the Function itself, no
                    # indirection). Try the direct (Function) case first,
                    # fall back to the Part-indirection (Entity) case on
                    # ClassCastException -- avoids hardcoding a type check
                    # that would need updating for every future represented
                    # type (Actor, Role, ...).
                    feature = el.get_java_object().eClass().getEStructuralFeature("representedInstance")
                    try:
                        el.get_java_object().eSet(feature, represented.get_java_object())
                    except Exception:
                        # BUG (found 2026-08-17 via a real OES lifeline
                        # header rendering "OA 2"/"OA 3" instead of the
                        # represented Entity's real name): CapellaServices.
                        # creationService() auto-creates the Entity's
                        # self-representing Part with an auto-generated
                        # placeholder name ("OA 2", never the Entity's own
                        # name) -- and once that Part exists, Capella's
                        # label-computation service for the Entity itself
                        # (get_label(), and Sirius's own
                        # ScenarioService.getInstanceRoleLabel() used by the
                        # odesign's lifeline-header labelExpression) reads
                        # THROUGH to the Part's name instead of the Entity's
                        # own -- confirmed live: even the Entity's raw
                        # getName() reads back as "OA 2" after
                        # creationService() runs. So the represented
                        # Entity's real name MUST be captured before this
                        # call, not re-read after -- re-reading after would
                        # silently capture the already-corrupted "OA 2"
                        # instead. Naming the (possibly pre-existing, shared/
                        # reused across every InstanceRole ever pointed at
                        # this Entity) Part fixes both the Part's own name
                        # and the Entity's derived getName()/get_label() in
                        # one write.
                        represented_name = represented.get_java_object().getName()
                        capella_services = org.polarsys.capella.core.sirius.analysis.CapellaServices.getService()
                        capella_services.creationService(represented.get_java_object())
                        part = represented.get_java_object().getAbstractTypedElements().get(0)
                        if represented_name and part.getName() != represented_name:
                            part.setName(represented_name)
                        el.get_java_object().eSet(feature, part)
                    container.get_owned_instance_roles().add(el)
                elif {type_name!r} == "SequenceMessage":
                    # The most complex branch here -- a SequenceMessage has
                    # no simple settable source/target. Real shape (per
                    # Interaction.ecore): Scenario.ownedEvents holds
                    # EventSentOperation/EventReceiptOperation;
                    # Scenario.ownedInteractionFragments holds a MessageEnd
                    # pair (list order = semantic temporal order); each
                    # MessageEnd.event points at its Event,
                    # MessageEnd.coveredInstanceRoles (real, required) at
                    # its InstanceRole. None of EventSentOperation/
                    # EventReceiptOperation/MessageEnd have a capella.py
                    # wrapper class -- built via EMF_API.py's
                    # create_e_object(), the same primitive capella.py's
                    # own constructors use internally.
                    if {parent_id!r} is None:
                        raise ValueError("SequenceMessage requires parent_id (an existing Scenario)")
                    if not hasattr(container, "get_owned_messages"):
                        raise AttributeError(
                            "no get_owned_messages() found on "
                            f"{{type(container).__name__}} (parent_id must be a Scenario)"
                        )
                    attrs = {attributes!r} or {{}}
                    source_id = attrs.get("source_id")
                    target_id = attrs.get("target_id")
                    kind = attrs.get("kind", "SYNCHRONOUS_CALL")
                    if not source_id or not target_id:
                        raise ValueError(
                            "SequenceMessage requires attributes={{'source_id': ..., 'target_id': ...}} "
                            "(element ids of two existing InstanceRoles in the same Scenario)"
                        )
                    source_ir = target_ir = None
                    for candidate in layer_obj.get_all_contents() if hasattr(layer_obj, "get_all_contents") else []:
                        cid = _element_id(candidate)
                        if cid == source_id:
                            source_ir = candidate
                        if cid == target_id:
                            target_ir = candidate
                    if source_ir is None:
                        raise ValueError(f"source_id not found in layer: {{source_id}}")
                    if target_ir is None:
                        raise ValueError(f"target_id not found in layer: {{target_id}}")

                    ns = "http://www.polarsys.org/capella/core/interaction/" + capella_version()
                    send_event = create_e_object(ns, "EventSentOperation")
                    container.get_java_object().getOwnedEvents().add(send_event)
                    recv_event = create_e_object(ns, "EventReceiptOperation")
                    container.get_java_object().getOwnedEvents().add(recv_event)

                    send_end = create_e_object(ns, "MessageEnd")
                    send_end.setEvent(send_event)
                    send_end.getCoveredInstanceRoles().add(source_ir.get_java_object())
                    recv_end = create_e_object(ns, "MessageEnd")
                    recv_end.setEvent(recv_event)
                    recv_end.getCoveredInstanceRoles().add(target_ir.get_java_object())
                    container.get_java_object().getOwnedInteractionFragments().add(send_end)
                    container.get_java_object().getOwnedInteractionFragments().add(recv_end)

                    el.get_java_object().setKind(get_enum_literal(ns, "MessageKind", kind))
                    el.get_java_object().setSendingEnd(send_end)
                    el.get_java_object().setReceivingEnd(recv_end)
                    container.get_owned_messages().add(el)
                else:
                    container.get_contents().append(el)
                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()
            _write_result(_serialize(el))
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def update_element(model_path: str, element_id: str, attributes: dict) -> dict:
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            target_id = {element_id!r}
            se = model.get_system_engineering()
            found = None
            for method_name in {list(LAYER_METHODS.values())!r}:
                layer = getattr(se, method_name)()
                if layer is None:
                    continue
                for el in layer.get_all_contents() if hasattr(layer, "get_all_contents") else []:
                    if _element_id(el) == target_id:
                        found = el
                        break
                if found is not None:
                    break
            if found is None:
                _write_result({{"error": f"element not found: {{target_id}}"}})
            else:
                model.start_transaction()
                try:
                    for key, value in ({attributes!r} or {{}}).items():
                        setter = getattr(found, f"set_{{key}}", None)
                        if setter is not None:
                            setter(value)
                    model.commit_transaction()
                except Exception:
                    model.rollback_transaction()
                    raise
                model.save()
                _write_result(_serialize(found))
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def _diagram_include() -> str:
    return "include('workspace://Python4Capella/simplified_api/diagram.py')\n"


def create_diagram(
    model_path: str,
    layer: str,
    type_name: str | None = None,
    root_id: str | None = None,
    include_relations: bool = True,
    diagram_name: str | None = None,
    max_depth: int | None = None,
) -> dict:
    """Create a real Capella (Sirius) breakdown diagram, fully automatic
    (including layout -- python4capella has no auto-arrange, so bounds are
    computed by _layout_tree and written explicitly).

    Internally chains 3 headless Capella calls (see docs/second_brain and
    the upgrade plan for why): GMF notation views (needed for set_bounds)
    only materialize after a save()+reopen, never within the same
    transaction they were created in.
    """
    if layer not in LAYER_METHODS:
        raise BridgeError(f"unknown layer {layer!r}, expected one of {sorted(LAYER_METHODS)}")
    if root_id is None and type_name is None:
        raise BridgeError("create_diagram requires type_name when root_id is not given")
    if type_name is not None and root_id is None and (layer, type_name) not in BREAKDOWN_DIAGRAMS:
        raise BridgeError(
            f"no breakdown diagram known for (layer={layer!r}, type_name={type_name!r}); "
            f"known combinations: {sorted(BREAKDOWN_DIAGRAMS)}"
        )
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    layer_method = LAYER_METHODS[layer]

    # Pass 1: resolve root + type_name, walk containment/relations in pure
    # Python (no layout yet), create the representation, save.
    pass1_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layer_obj = getattr(se, {layer_method!r})()
            breakdown_diagrams = {BREAKDOWN_DIAGRAMS!r}
            root_id = {root_id!r}
            type_name = {type_name!r}
            max_depth = {max_depth!r}

            if root_id:
                root = None
                for el in layer_obj.get_all_contents():
                    if _element_id(el) == root_id:
                        root = el
                        break
                if root is None:
                    raise ValueError(f"root_id not found in layer: {{root_id}}")
                type_name = type(root).__name__
                cfg = breakdown_diagrams.get(({layer!r}, type_name))
                if cfg is None:
                    raise ValueError(f"no breakdown diagram known for type {{type_name}} in layer {layer!r}")
            else:
                cfg = breakdown_diagrams[({layer!r}, type_name)]
                pkg = getattr(layer_obj, cfg["pkg_method"])()
                candidates = list(getattr(pkg, cfg["owned_root_method"])())
                if len(candidates) == 0:
                    raise ValueError("no root-level element of this type found; pass root_id")
                if len(candidates) > 1:
                    raise ValueError(
                        f"multiple root-level elements found ({{len(candidates)}}); pass root_id to pick one"
                    )
                root = candidates[0]

            root_id_resolved = _element_id(root)
            nodes_by_id = {{}}
            tree = []

            def _walk(el, parent_id, depth):
                eid = _element_id(el)
                nodes_by_id[eid] = el
                tree.append({{"id": eid, "label": el.get_label() or eid, "parent_id": parent_id, "depth": depth}})
                if max_depth is not None and depth >= max_depth:
                    return
                children_method = cfg["children_method"]
                if hasattr(el, children_method):
                    for child in getattr(el, children_method)():
                        _walk(child, eid, depth + 1)

            _walk(root, None, 0)

            relations = []
            if {include_relations!r}:
                seen = set()
                for eid, el in nodes_by_id.items():
                    if not hasattr(el, "get_owned_functional_exchanges"):
                        continue
                    for exch in el.get_owned_functional_exchanges():
                        try:
                            src = exch.get_source_function()
                            tgt = exch.get_target_function()
                        except Exception:
                            continue
                        if src is None or tgt is None:
                            continue
                        src_id = _element_id(src)
                        tgt_id = _element_id(tgt)
                        pair = (src_id, tgt_id)
                        if src_id in nodes_by_id and tgt_id in nodes_by_id and pair not in seen:
                            seen.add(pair)
                            relations.append({{"source_id": src_id, "target_id": tgt_id, "label": exch.get_label() or ""}})

            repDef = get_representation_definition_by_name(model.session, cfg["diagram"])
            if repDef is None:
                raise ValueError(f"representation definition not found: {{cfg['diagram']}}")
            resolved_diagram_name = {diagram_name!r} or f"{{type_name}} Breakdown - {{root.get_label()}}"

            model.start_transaction()
            try:
                create_representation(model.session, root, repDef, resolved_diagram_name)
                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            _write_result({{
                "type_name": type_name,
                "root_id": root_id_resolved,
                "tree": tree,
                "relations": relations,
                "diagram_name": resolved_diagram_name,
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass1 = _run_script(pass1_body)

    type_name = pass1["type_name"]
    root_id_resolved = pass1["root_id"]
    tree = pass1["tree"]
    relations = pass1["relations"]
    resolved_diagram_name = pass1["diagram_name"]
    cfg = BREAKDOWN_DIAGRAMS[(layer, type_name)]
    bounds_by_id = _layout_tree(tree)

    # Pass 2 (reopen): reconcile nodes (skip anything the diagram already
    # auto-populated on its own -- Capella breakdown diagrams synchronize
    # their direct semantic children on save regardless of the
    # `synchronized` flag; adding it again via apply_mapping duplicates
    # it), add containment + cross-relation edges, set bounds for whatever
    # already has a GMF node (i.e. existed before this pass).
    pass2_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layer_obj = getattr(se, {layer_method!r})()
            cfg = {cfg!r}
            tree = {tree!r}
            relations = {relations!r}
            bounds_by_id = {bounds_by_id!r}
            root_id = {root_id_resolved!r}

            elements_by_id = {{}}
            for el in layer_obj.get_all_contents():
                elements_by_id[_element_id(el)] = el

            diagrams = model.get_all_diagrams()
            target = None
            for d in diagrams:
                if d.get_name() == {resolved_diagram_name!r}:
                    target = d
                    break
            if target is None:
                raise ValueError(f"diagram not found after pass 1: {resolved_diagram_name!r}")
            java_diag = target.get_java_object().getRepresentation()

            existing_dnodes = {{}}
            for de in java_diag.getOwnedDiagramElements():
                if de.eClass().getName() != "DEdge":
                    existing_dnodes[de.getTarget().getId()] = de

            repDef = get_representation_definition_by_name(model.session, cfg["diagram"])
            nodeMapping = get_representation_mapping_by_name(repDef, cfg["node_mapping"])
            edgeMapping = get_representation_mapping_by_name(repDef, cfg["edge_mapping"])

            new_node_ids = []
            model.start_transaction()
            try:
                for node in tree:
                    nid = node["id"]
                    if nid == root_id or nid in existing_dnodes:
                        # root itself is never shown as a node (matches
                        # Capella's own native breakdown diagrams -- the
                        # diagram's target is implicit, only its
                        # descendants get boxes).
                        continue
                    el = elements_by_id.get(nid)
                    if el is None:
                        continue
                    dnode = apply_mapping(java_diag, nodeMapping, el.get_java_object())
                    existing_dnodes[nid] = dnode
                    new_node_ids.append(nid)

                # Containment edges are intentionally NOT created here --
                # Capella auto-wires them itself on save whenever both the
                # parent and child already have a DNode in this diagram
                # (confirmed empirically: creating a grandchild node
                # manually was enough to make Capella also add its edge to
                # the already-auto-populated parent node, in Capella's own
                # child->parent direction -- doing it ourselves too just
                # duplicated the edge, backwards).

                for rel in relations:
                    src_id = rel["source_id"]
                    tgt_id = rel["target_id"]
                    if src_id not in existing_dnodes or tgt_id not in existing_dnodes:
                        continue
                    tgt_el = elements_by_id.get(tgt_id)
                    if tgt_el is None:
                        continue
                    dedge = apply_mapping(java_diag, edgeMapping, tgt_el.get_java_object())
                    dedge.setSourceNode(existing_dnodes[src_id])
                    dedge.setTargetNode(existing_dnodes[tgt_id])

                for nid, dnode in existing_dnodes.items():
                    if nid in new_node_ids:
                        continue
                    bounds = bounds_by_id.get(nid)
                    if bounds is not None:
                        set_bounds(dnode, bounds)

                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()
            _write_result({{"new_node_ids": new_node_ids}})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass2 = _run_script(pass2_body)
    new_node_ids = pass2["new_node_ids"]

    # Pass 3 (reopen): set bounds for nodes created in pass 2 (their GMF
    # node only exists now, after that pass's save), and fetch the stable
    # diagram uid.
    pass3_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            bounds_by_id = {bounds_by_id!r}
            new_node_ids = {new_node_ids!r}

            diagrams = model.get_all_diagrams()
            target = None
            for d in diagrams:
                if d.get_name() == {resolved_diagram_name!r}:
                    target = d
                    break
            if target is None:
                raise ValueError(f"diagram not found after pass 2: {resolved_diagram_name!r}")
            java_diag = target.get_java_object().getRepresentation()

            if new_node_ids:
                by_target_id = {{}}
                for de in java_diag.getOwnedDiagramElements():
                    if de.eClass().getName() != "DEdge":
                        by_target_id[de.getTarget().getId()] = de
                model.start_transaction()
                try:
                    for nid in new_node_ids:
                        dnode = by_target_id.get(nid)
                        bounds = bounds_by_id.get(nid)
                        if dnode is not None and bounds is not None:
                            set_bounds(dnode, bounds)
                    model.commit_transaction()
                except Exception:
                    model.rollback_transaction()
                    raise
                model.save()

            node_count = sum(1 for de in java_diag.getOwnedDiagramElements() if de.eClass().getName() != "DEdge")
            edge_count = sum(1 for de in java_diag.getOwnedDiagramElements() if de.eClass().getName() == "DEdge")
            _write_result({{
                "diagram_uid": target.get_uid(),
                "diagram_name": target.get_name(),
                "node_count": node_count,
                "edge_count": edge_count,
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass3 = _run_script(pass3_body)
    return {
        "diagram_uid": pass3["diagram_uid"],
        "diagram_name": pass3["diagram_name"],
        "type_name": type_name,
        "root_id": root_id_resolved,
        "node_count": pass3["node_count"],
        "edge_count": pass3["edge_count"],
    }


def create_container_diagram(
    model_path: str,
    layer: str,
    type_name: str,
    diagram_name: str | None = None,
    max_depth: int | None = None,
) -> dict:
    """Create a real Capella (Sirius) "Blank" diagram -- the ContainerMapping
    family (Operational Entity Blank, Operational Activity Interaction
    Blank; see CONTAINER_DIAGRAMS' comment for the full picture), as
    opposed to create_diagram's NodeMapping-based "breakdown" trees.

    Unlike create_diagram, this walks and populates the WHOLE forest of
    root-level elements of this type (a Blank diagram isn't scoped to one
    root the way a breakdown diagram is -- it shows the package's entire
    structure, same as Capella's own "New Diagram > Operational Entity
    Blank" wizard entry would from the Entities package).

    Renders correctly headless (see CONTAINER_DIAGRAMS' comment for the
    root-cause investigation and fix -- DiagramServices.createContainer()
    instead of python4capella's apply_mapping()).
    """
    if layer not in LAYER_METHODS:
        raise BridgeError(f"unknown layer {layer!r}, expected one of {sorted(LAYER_METHODS)}")
    if (layer, type_name) not in CONTAINER_DIAGRAMS:
        raise BridgeError(
            f"no container/blank diagram known for (layer={layer!r}, type_name={type_name!r}); "
            f"known combinations: {sorted(CONTAINER_DIAGRAMS)}"
        )
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    layer_method = LAYER_METHODS[layer]
    cfg = CONTAINER_DIAGRAMS[(layer, type_name)]

    # Pass 1: resolve every root-level element of this type (a forest, not
    # a single root), create the representation targeted at the owning
    # package, then walk + apply_mapping the whole forest in the SAME
    # transaction -- create_representation() returns the raw Java
    # DRepresentation directly (confirmed by reading diagram.py, not
    # wrapped the way model.get_all_diagrams()' Diagram objects are), so no
    # reopen is needed just to start adding nodes to it, only for
    # set_bounds afterwards (GMF notation views still only materialize
    # after save()+reopen, same as create_diagram).
    pass1_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layer_obj = getattr(se, {layer_method!r})()
            cfg = {cfg!r}
            max_depth = {max_depth!r}

            pkg = getattr(layer_obj, cfg["pkg_method"])()
            roots = list(getattr(pkg, cfg["owned_root_method"])())
            if not roots:
                raise ValueError("no root-level element of this type found")

            repDef = get_representation_definition_by_name(model.session, cfg["diagram"])
            if repDef is None:
                raise ValueError(f"representation definition not found: {{cfg['diagram']}}")
            containerMapping = get_representation_mapping_by_name(repDef, cfg["container_mapping"])
            if containerMapping is None:
                raise ValueError(f"container mapping not found: {{cfg['container_mapping']}}")
            resolved_diagram_name = {diagram_name!r} or f"{{{type_name!r}}} Blank - {{pkg.get_label()}}"

            edgeMapping = None
            if cfg.get("edge_mapping"):
                edgeMapping = get_representation_mapping_by_name(repDef, cfg["edge_mapping"])
                if edgeMapping is None:
                    raise ValueError(f"edge mapping not found: {{cfg['edge_mapping']}}")

            # DiagramServices.createContainer() (Capella's own public
            # service, not python4capella's apply_mapping()) is what fixes
            # the known-blank-PNG limitation documented above: it routes
            # through the real DDiagramSynchronizer/DDiagramElementSynchronizer
            # pipeline instead of apply_mapping()'s raw EMF instantiation,
            # which never gets ownedStyle populated and never gets a fully
            # materialized GMF view. Confirmed live (2026-08-15): identical
            # containers created via apply_mapping() exported blank;
            # created via this call, they render with a real border, fill,
            # icon, and label -- both flat and nested. Edges/NodeMapping
            # nodes elsewhere in this module still use apply_mapping()
            # since those were never observed broken.
            diagram_services = org.polarsys.capella.core.sirius.analysis.DiagramServices.getDiagramServices()

            tree = []
            elements_by_id = {{}}
            dnodes_by_id = {{}}

            def _walk_apply(el, container_java, parent_id, depth):
                eid = _element_id(el)
                tree.append({{"id": eid, "label": el.get_label() or eid, "parent_id": parent_id, "depth": depth}})
                elements_by_id[eid] = el
                dnode = diagram_services.createContainer(containerMapping, el.get_java_object(), container_java, java_diag)
                dnodes_by_id[eid] = dnode
                if max_depth is not None and depth >= max_depth:
                    return
                children_method = cfg["children_method"]
                if hasattr(el, children_method):
                    for child in getattr(el, children_method)():
                        _walk_apply(child, dnode, eid, depth + 1)

            relations = []
            if edgeMapping is not None:
                # Same cross-node functional-exchange collection as
                # create_diagram's include_relations, restricted to pairs
                # where both ends are actually placed in this diagram.
                seen = set()
                for eid, el in elements_by_id.items():
                    if not hasattr(el, "get_owned_functional_exchanges"):
                        continue
                    for exch in el.get_owned_functional_exchanges():
                        try:
                            src = exch.get_source_function()
                            tgt = exch.get_target_function()
                        except Exception:
                            continue
                        if src is None or tgt is None:
                            continue
                        src_id = _element_id(src)
                        tgt_id = _element_id(tgt)
                        pair = (src_id, tgt_id)
                        if src_id in elements_by_id and tgt_id in elements_by_id and pair not in seen:
                            seen.add(pair)
                            relations.append({{"source_id": src_id, "target_id": tgt_id, "label": exch.get_label() or ""}})

            model.start_transaction()
            try:
                # The representation's target must be an element compatible
                # with this diagram kind's domainClass -- the owning
                # package isn't always accepted (createRepresentation
                # returns None silently rather than raising when it isn't,
                # confirmed live for OAIB), so an actual root element of
                # this diagram's own type is the safer, more broadly
                # compatible anchor. The diagram's *displayed* content is
                # still the whole forest below, added via apply_mapping
                # regardless of which single element was the target.
                java_diag = create_representation(model.session, roots[0], repDef, resolved_diagram_name)
                if java_diag is None:
                    raise ValueError(
                        f"createRepresentation returned None for {{cfg['diagram']}} "
                        "(target element incompatible with this representation's domainClass)"
                    )
                for root in roots:
                    _walk_apply(root, java_diag, None, 0)
                for rel in relations:
                    tgt_el = elements_by_id[rel["target_id"]]
                    dedge = apply_mapping(java_diag, edgeMapping, tgt_el.get_java_object())
                    dedge.setSourceNode(dnodes_by_id[rel["source_id"]])
                    dedge.setTargetNode(dnodes_by_id[rel["target_id"]])
                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            _write_result({{
                "type_name": {type_name!r},
                "tree": tree,
                "relations": relations,
                "diagram_name": resolved_diagram_name,
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass1 = _run_script(pass1_body)

    tree = pass1["tree"]
    resolved_diagram_name = pass1["diagram_name"]
    bounds_by_id = _layout_tree(tree)  # already forest-aware (roots = children[None])

    # Pass 2 (reopen): every node was created in pass 1 (this diagram kind
    # is never auto-synchronized, unlike breakdown diagrams), so its GMF
    # node only exists now -- set bounds for all of them, fetch stable uid.
    pass2_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            bounds_by_id = {bounds_by_id!r}

            diagrams = model.get_all_diagrams()
            target = None
            for d in diagrams:
                if d.get_name() == {resolved_diagram_name!r}:
                    target = d
                    break
            if target is None:
                raise ValueError(f"diagram not found after pass 1: {resolved_diagram_name!r}")
            java_diag = target.get_java_object().getRepresentation()

            # Nested elements live inside their parent DNodeContainer's own
            # getOwnedDiagramElements() (same accessor apply_mapping already
            # uses to nest into a container), NOT in java_diag's top-level
            # list -- a flat scan here would silently miss/skip bounds for
            # everything but root-level entities.
            by_target_id = {{}}

            def _collect(de):
                if de.eClass().getName() != "DEdge":
                    by_target_id[de.getTarget().getId()] = de
                    # NOTE: hasattr(de, "getOwnedDiagramElements") is NOT a
                    # safe guard here -- Py4J's dynamic proxy reports it
                    # True generically for any Java object regardless of
                    # whether the concrete class actually has the method,
                    # and only fails when called (confirmed live: DNode,
                    # e.g. a free-floating capability node, does not have
                    # this method the way DNodeContainer does, but hasattr
                    # claimed it did). Check the real EMF class instead.
                    if de.eClass().getName() == "DNodeContainer":
                        for child in de.getOwnedDiagramElements():
                            _collect(child)

            for de in java_diag.getOwnedDiagramElements():
                _collect(de)

            model.start_transaction()
            try:
                for nid, bounds in bounds_by_id.items():
                    dnode = by_target_id.get(nid)
                    if dnode is not None:
                        set_bounds(dnode, bounds)
                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            edge_count = sum(1 for de in java_diag.getOwnedDiagramElements() if de.eClass().getName() == "DEdge")
            _write_result({{
                "diagram_uid": target.get_uid(),
                "diagram_name": target.get_name(),
                "node_count": len(by_target_id),
                "edge_count": edge_count,
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass2 = _run_script(pass2_body)
    return {
        "diagram_uid": pass2["diagram_uid"],
        "diagram_name": pass2["diagram_name"],
        "type_name": pass1["type_name"],
        "node_count": pass2["node_count"],
        "edge_count": pass2["edge_count"],
    }


# Class Diagram Blank (CDB, common.odesign -- not OA-specific, but every
# layer including "oa" has its own default DataPkg via
# <Layer>.get_data_pkg()). Distinct from create_container_diagram: CDB's
# tree is HETEROGENEOUS (DataPkg containers nesting *both* sub-DataPkgs and
# Class leaves -- two different ContainerMappings, DT_DataPkg/DT_Class, not
# CONTAINER_DIAGRAMS' single container_mapping shape), so it gets its own
# function rather than forcing that config table to express two node
# kinds for just this one case.
#
# Same fix as CONTAINER_DIAGRAMS (see its comment): both DT_DataPkg and
# DT_Class are ContainerMappings, so element creation below uses
# DiagramServices.createContainer() instead of apply_mapping() -- renders
# correctly headless, verified live.
_CLASS_DIAGRAM_NAME = "Class Diagram Blank"
_CLASS_DIAGRAM_PKG_MAPPING = "DT_DataPkg"
_CLASS_DIAGRAM_CLASS_MAPPING = "DT_Class"


def create_class_diagram(
    model_path: str,
    layer: str,
    diagram_name: str | None = None,
    max_depth: int | None = None,
) -> dict:
    """Create a real Capella (Sirius) "Class Diagram Blank" and save the
    model, rooted at the layer's own default DataPkg -- recurses through
    nested DataPkgs and the Classes owned at every level.

    layer must be one of: oa, sa, la, pa, epbs (every layer has a DataPkg).

    Renders correctly headless (see CONTAINER_DIAGRAMS' comment in this
    module for the root-cause investigation and fix).
    """
    if layer not in LAYER_METHODS:
        raise BridgeError(f"unknown layer {layer!r}, expected one of {sorted(LAYER_METHODS)}")
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    layer_method = LAYER_METHODS[layer]

    # Pass 1: resolve the layer's default DataPkg, create the
    # representation targeted at it, then walk + apply_mapping the whole
    # heterogeneous tree (DataPkg -> sub-DataPkgs + Classes) in the same
    # transaction -- same reasoning as create_container_diagram for why no
    # reopen is needed until set_bounds.
    pass1_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layer_obj = getattr(se, {layer_method!r})()
            max_depth = {max_depth!r}

            root_pkg = layer_obj.get_data_pkg()
            if root_pkg is None:
                raise ValueError("this layer has no DataPkg")

            repDef = get_representation_definition_by_name(model.session, {_CLASS_DIAGRAM_NAME!r})
            if repDef is None:
                raise ValueError("representation definition not found: {_CLASS_DIAGRAM_NAME}")
            pkgMapping = get_representation_mapping_by_name(repDef, {_CLASS_DIAGRAM_PKG_MAPPING!r})
            classMapping = get_representation_mapping_by_name(repDef, {_CLASS_DIAGRAM_CLASS_MAPPING!r})
            if pkgMapping is None or classMapping is None:
                raise ValueError("container mapping not found: {_CLASS_DIAGRAM_PKG_MAPPING}/{_CLASS_DIAGRAM_CLASS_MAPPING}")
            resolved_diagram_name = {diagram_name!r} or f"{{{_CLASS_DIAGRAM_NAME!r}}} - {{root_pkg.get_label()}}"

            # See create_container_diagram's comment for why
            # DiagramServices.createContainer() replaces apply_mapping()
            # here -- both DT_DataPkg and DT_Class are ContainerMappings.
            diagram_services = org.polarsys.capella.core.sirius.analysis.DiagramServices.getDiagramServices()

            tree = []

            def _walk_apply(el, container_java, parent_id, depth, kind):
                eid = _element_id(el)
                tree.append({{"id": eid, "label": el.get_label() or eid, "parent_id": parent_id, "depth": depth, "kind": kind}})
                mapping = pkgMapping if kind == "DataPkg" else classMapping
                dnode = diagram_services.createContainer(mapping, el.get_java_object(), container_java, java_diag)
                if max_depth is not None and depth >= max_depth:
                    return
                if kind == "DataPkg":
                    for sub_pkg in el.get_owned_data_pkgs():
                        _walk_apply(sub_pkg, dnode, eid, depth + 1, "DataPkg")
                    for cls in el.get_owned_classes():
                        _walk_apply(cls, dnode, eid, depth + 1, "Class")

            model.start_transaction()
            try:
                java_diag = create_representation(model.session, root_pkg, repDef, resolved_diagram_name)
                _walk_apply(root_pkg, java_diag, None, 0, "DataPkg")
                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            _write_result({{
                "tree": tree,
                "diagram_name": resolved_diagram_name,
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass1 = _run_script(pass1_body)

    tree = pass1["tree"]
    resolved_diagram_name = pass1["diagram_name"]
    bounds_by_id = _layout_tree(tree)

    # Pass 2 (reopen): set bounds for every node (never auto-synchronized,
    # same reasoning as create_container_diagram).
    pass2_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            bounds_by_id = {bounds_by_id!r}

            diagrams = model.get_all_diagrams()
            target = None
            for d in diagrams:
                if d.get_name() == {resolved_diagram_name!r}:
                    target = d
                    break
            if target is None:
                raise ValueError(f"diagram not found after pass 1: {resolved_diagram_name!r}")
            java_diag = target.get_java_object().getRepresentation()

            by_target_id = {{}}

            def _collect(de):
                if de.eClass().getName() != "DEdge":
                    by_target_id[de.getTarget().getId()] = de
                    # NOTE: hasattr(de, "getOwnedDiagramElements") is NOT a
                    # safe guard here -- Py4J's dynamic proxy reports it
                    # True generically for any Java object regardless of
                    # whether the concrete class actually has the method,
                    # and only fails when called (confirmed live: DNode,
                    # e.g. a free-floating capability node, does not have
                    # this method the way DNodeContainer does, but hasattr
                    # claimed it did). Check the real EMF class instead.
                    if de.eClass().getName() == "DNodeContainer":
                        for child in de.getOwnedDiagramElements():
                            _collect(child)

            for de in java_diag.getOwnedDiagramElements():
                _collect(de)

            model.start_transaction()
            try:
                for nid, bounds in bounds_by_id.items():
                    dnode = by_target_id.get(nid)
                    if dnode is not None:
                        set_bounds(dnode, bounds)
                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            _write_result({{
                "diagram_uid": target.get_uid(),
                "diagram_name": target.get_name(),
                "node_count": len(by_target_id),
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass2 = _run_script(pass2_body)
    return {
        "diagram_uid": pass2["diagram_uid"],
        "diagram_name": pass2["diagram_name"],
        "node_count": pass2["node_count"],
    }


# Operational Capabilities Blank (OCB, oa.odesign). Gets its own function
# rather than a CONTAINER_DIAGRAMS entry for two reasons: (1) its
# containerMapping (COC_OperationalEntities) targets the same
# OperationalEntity/OperationalActor types create_container_diagram's OAB
# entry already owns as a dict key -- OAB and OCB are two DIFFERENT
# representations over the SAME semantic types, which CONTAINER_DIAGRAMS'
# (layer, type_name) keying can't express; (2) OCB isn't a homogeneous
# containment tree at all -- see the shape correction below.
#
# SHAPE CORRECTED (2026-08-16): the first version of this function nested
# each involving capability inside its entity's container (apply_mapping()
# targeting the entity's DNodeContainer). That does not match oa.odesign's
# real definition of this diagram, and the mismatch only surfaced against a
# real populated model (car_hmi.aird has zero OperationalCapability/
# involvement data, so this path was never actually exercised by this
# project's own tests) -- caught via another agent's live troubleshooting
# against a populated model, cross-checked directly against oa.odesign:
#   - COC_OperationalCapabilities is a free NodeMapping (createElements=
#     "false", semanticElements="", WorkspaceImageDescription style) --
#     designed to be a loose node on the canvas, not a child of a container.
#   - COC_EntityOperationalCapabilityInvolvement is an EdgeMapping
#     (sourceMapping=COC_OperationalCapabilities, targetMapping=
#     COC_OperationalEntities) -- the involvement itself is a drawn edge,
#     not containment.
# Nesting the capability inside the entity container used a technically
# "successful" apply_mapping() call that didn't match this shape, which is
# consistent with why it broke down further once used non-headlessly:
# corrupted bounds on drag (GMF's SetConnectionBendpointsCommand assumes
# every edge's notation:Edge already has non-null bendpoints, which only a
# real edge-creation call populates) and elements Capella's own Desktop
# never displayed.
#
# Correct shape, implemented below: entities/actors as containers
# (unchanged from the first version, DiagramServices.createContainer() --
# same fix as CONTAINER_DIAGRAMS), capabilities as free nodes placed
# directly on the diagram root (DiagramServices.createNode(), deduped by
# id so a capability involved by more than one entity gets exactly one
# node, not one per entity), and involvement edges
# (DiagramServices.createEdge(), source=capability node, target=entity
# container, per oa.odesign's own source/targetMapping) connecting them --
# same real DDiagramSynchronizer/DDiagramElementSynchronizer pipeline
# createContainer() already uses, for the same reason: apply_mapping()
# alone does not reliably produce a fully-formed diagram element.
#
# Communication-means edges (COC_CommunicationMeans) and capability-to-
# capability relations (COC_OC_Extends/Include/Generalization) are not
# collected -- only the core entity-involves-capability structure, same
# "one representative edge/relation kind, not every possible one" scope
# OAIB's "OAIB Interaction" already established.
_CAPABILITY_DIAGRAM_NAME = "Operational Capabilities Blank"
_CAPABILITY_ENTITY_MAPPING = "COC_OperationalEntities"
_CAPABILITY_NODE_MAPPING = "COC_OperationalCapabilities"
_CAPABILITY_INVOLVEMENT_MAPPING = "COC_EntityOperationalCapabilityInvolvement"


def create_capability_diagram(model_path: str, diagram_name: str | None = None) -> dict:
    """Create a real Capella (Sirius) "Operational Capabilities Blank"
    diagram and save the model -- the whole forest of root Operational
    Entities/Actors as containers, every Operational Capability any of them
    is involved with as a free node (not nested -- see this module's
    comment above CONTAINER_DIAGRAMS-adjacent constants for why), and an
    involvement edge from each capability to every entity involved with it.

    OA-layer only (Operational Capabilities Blank is OA-specific, unlike
    create_class_diagram's CDB).

    Renders correctly headless (see CONTAINER_DIAGRAMS' comment in this
    module for the root-cause investigation and fix that applies to the
    entity containers here too).
    """
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)

    pass1_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            oa = se.get_operational_analysis()

            pkg = oa.get_entity_pkg()
            roots = list(pkg.get_owned_entities())
            if not roots:
                raise ValueError("no root-level Operational Entity/Actor found")

            repDef = get_representation_definition_by_name(model.session, {_CAPABILITY_DIAGRAM_NAME!r})
            if repDef is None:
                raise ValueError("representation definition not found: {_CAPABILITY_DIAGRAM_NAME}")
            entityMapping = get_representation_mapping_by_name(repDef, {_CAPABILITY_ENTITY_MAPPING!r})
            capabilityMapping = get_representation_mapping_by_name(repDef, {_CAPABILITY_NODE_MAPPING!r})
            involvementMapping = get_representation_mapping_by_name(repDef, {_CAPABILITY_INVOLVEMENT_MAPPING!r})
            if entityMapping is None or capabilityMapping is None or involvementMapping is None:
                raise ValueError("mapping not found: {_CAPABILITY_ENTITY_MAPPING}/{_CAPABILITY_NODE_MAPPING}/{_CAPABILITY_INVOLVEMENT_MAPPING}")
            resolved_diagram_name = {diagram_name!r} or f"{{{_CAPABILITY_DIAGRAM_NAME!r}}} - {{pkg.get_label()}}"

            diagram_services = org.polarsys.capella.core.sirius.analysis.DiagramServices.getDiagramServices()

            tree = []
            entity_dnodes = {{}}
            capabilities_by_id = {{}}
            involvements = []  # list of (entity_id, capability_id)

            def _walk_entities(el, container_java, parent_id, depth):
                eid = _element_id(el)
                tree.append({{"id": eid, "label": el.get_label() or eid, "parent_id": parent_id, "depth": depth, "kind": "Entity"}})
                dnode = diagram_services.createContainer(entityMapping, el.get_java_object(), container_java, java_diag)
                entity_dnodes[eid] = dnode

                if hasattr(el, "get_involving_operational_capabilities"):
                    for cap in el.get_involving_operational_capabilities():
                        cap_id = _element_id(cap)
                        capabilities_by_id[cap_id] = cap
                        involvements.append((eid, cap_id))

                if hasattr(el, "get_owned_entities"):
                    for child in el.get_owned_entities():
                        _walk_entities(child, dnode, eid, depth + 1)

            model.start_transaction()
            try:
                # Unlike OAB/OAIB (create_container_diagram), neither a
                # root Entity, the EntityPkg, nor the OA layer root are
                # accepted here (createRepresentation returns None for all
                # three, confirmed live via a diagnostic script trying 6
                # candidates) -- OCB's actual domainClass is
                # OperationalCapabilityPkg (makes sense: its primary
                # semantic scope is the capabilities, entities are the
                # cross-referenced/involved side).
                java_diag = create_representation(model.session, oa.get_operational_capability_pkg(), repDef, resolved_diagram_name)
                if java_diag is None:
                    raise ValueError("createRepresentation returned None for {_CAPABILITY_DIAGRAM_NAME}")
                for root in roots:
                    _walk_entities(root, java_diag, None, 0)

                # Capabilities are free nodes on the diagram root, one per
                # unique capability (never nested in an entity's container --
                # oa.odesign has no such nesting for COC_OperationalCapabilities).
                capability_dnodes = {{}}
                for cap_id, cap in capabilities_by_id.items():
                    cnode = diagram_services.createNode(capabilityMapping, cap.get_java_object(), java_diag, java_diag)
                    capability_dnodes[cap_id] = cnode
                    tree.append({{"id": cap_id, "label": cap.get_label() or cap_id, "parent_id": None, "depth": 1, "kind": "Capability"}})

                # Involvement is a drawn edge (source=capability,
                # target=entity, per oa.odesign's own source/targetMapping),
                # not containment.
                edge_count = 0
                for eid, cap_id in involvements:
                    diagram_services.createEdge(involvementMapping, capability_dnodes[cap_id], entity_dnodes[eid], capabilities_by_id[cap_id].get_java_object())
                    edge_count += 1

                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            _write_result({{
                "tree": tree,
                "diagram_name": resolved_diagram_name,
                "edge_count": edge_count,
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass1 = _run_script(pass1_body)

    tree = pass1["tree"]
    resolved_diagram_name = pass1["diagram_name"]
    edge_count = pass1["edge_count"]
    bounds_by_id = _layout_tree(tree)

    pass2_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            bounds_by_id = {bounds_by_id!r}

            diagrams = model.get_all_diagrams()
            target = None
            for d in diagrams:
                if d.get_name() == {resolved_diagram_name!r}:
                    target = d
                    break
            if target is None:
                raise ValueError(f"diagram not found after pass 1: {resolved_diagram_name!r}")
            java_diag = target.get_java_object().getRepresentation()

            by_target_id = {{}}

            def _collect(de):
                if de.eClass().getName() != "DEdge":
                    by_target_id[de.getTarget().getId()] = de
                    # NOTE: hasattr(de, "getOwnedDiagramElements") is NOT a
                    # safe guard here -- Py4J's dynamic proxy reports it
                    # True generically for any Java object regardless of
                    # whether the concrete class actually has the method,
                    # and only fails when called (confirmed live: DNode,
                    # e.g. a free-floating capability node, does not have
                    # this method the way DNodeContainer does, but hasattr
                    # claimed it did). Check the real EMF class instead.
                    if de.eClass().getName() == "DNodeContainer":
                        for child in de.getOwnedDiagramElements():
                            _collect(child)

            for de in java_diag.getOwnedDiagramElements():
                _collect(de)

            model.start_transaction()
            try:
                for nid, bounds in bounds_by_id.items():
                    dnode = by_target_id.get(nid)
                    if dnode is not None:
                        set_bounds(dnode, bounds)
                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            _write_result({{
                "diagram_uid": target.get_uid(),
                "diagram_name": target.get_name(),
                "node_count": len(by_target_id),
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass2 = _run_script(pass2_body)
    return {
        "diagram_uid": pass2["diagram_uid"],
        "diagram_name": pass2["diagram_name"],
        "node_count": pass2["node_count"],
        "edge_count": edge_count,
    }


# Sequence/scenario diagrams (OES "Operational Interaction Scenario", OAS
# "Activity Interaction Scenario"). RESOLVED (2026-08-17), after two earlier
# rounds concluded "blocked, no found public entry point" (see git history
# for the prior version of this comment) -- the semantic-side blockers
# (InstanceRole.representedInstance, SequenceMessage construction) and the
# export-side NPE both have concrete, headless-callable fixes now:
#
#   - InstanceRole.representedInstance wants an AbstractInstance, but which
#     concrete type varies: OES's "component"/"actor" tools represent an
#     Entity, which does NOT implement AbstractInstance itself (confirmed
#     live: direct eSet(representedInstance, entity) raises
#     ClassCastException) -- every Entity/Component instead has its own
#     self-representing Part, lazily created via
#     org.polarsys.capella.core.sirius.analysis.CapellaServices.getService()
#     .creationService(entity) and exposed via entity.getAbstractTypedElements()
#     (mirrors the odesign's own operation, `component.abstractTypedElements
#     ->at(1)`). OAS's "activity" tool, in contrast, sets representedInstance
#     directly to the OperationalActivity itself (odesign: `component`, no
#     indirection) -- AbstractFunction (OperationalActivity/SystemFunction/
#     LogicalFunction's common ancestor) directly extends Information.ecore's
#     AbstractInstance in its own eSuperTypes list, unlike Entity.
#     create_element()'s InstanceRole branch tries the direct eSet() first
#     and falls back to the Part-indirection path on ClassCastException,
#     rather than hardcoding a type check. Also, Py4J can't resolve
#     setRepresentedInstance's overload (interface-typed parameter vs a
#     concrete arg class) either way -- set via raw EMF eSet(), not the
#     named setter.
#
#   - COSMETIC BUG, found and fixed 2026-08-17: OES lifeline headers
#     rendered a generic placeholder ("OA 2", "OA 3", ...) instead of the
#     represented Entity's real name (OAS was never affected -- its
#     InstanceRoles represent the Function directly, no Part involved).
#     Root cause: CapellaServices.creationService() auto-creates the
#     Entity's self-representing Part with an auto-generated placeholder
#     name, never the Entity's own -- and once that Part exists, Capella's
#     label-computation service for the Entity ITSELF (get_label(), and
#     Sirius's own ScenarioService.getInstanceRoleLabel() that the odesign's
#     lifeline-header labelExpression calls) reads through to the Part's
#     name instead of the Entity's -- confirmed live: even the Entity's raw
#     getName() reads back as the placeholder after creationService() runs.
#     Fix: capture the Entity's real name BEFORE calling creationService()
#     (re-reading after would silently capture the already-corrupted
#     placeholder), then name the Part to match. Since the Part is
#     lazily-created-once-and-reused across every InstanceRole that ever
#     represents that Entity (any Scenario), this also retroactively fixes
#     every InstanceRole already pointing at a previously-corrupted Part.
#
#   - SequenceMessage construction: no simple settable source/target: build
#     a real EventSentOperation/EventReceiptOperation pair
#     (Scenario.ownedEvents) and a MessageEnd pair (Scenario.
#     ownedInteractionFragments, list order = semantic temporal order),
#     wired via SequenceMessage.sendingEnd/receivingEnd. None of these three
#     types have a capella.py wrapper -- built via EMF_API.py's
#     create_e_object(), the same primitive capella.py's own constructors
#     use internally. See create_element()'s SequenceMessage branch. To read
#     the InstanceRole back out of an existing message/end, use
#     MessageEnd.getCoveredInstanceRoles() (raw EMF) -- capella.py's own
#     SequenceMessage.get_sending_instance_role()/get_receiving_instance_role()
#     are misleadingly named: despite the name they resolve all the way
#     through to the represented Entity/Function (Interaction.ecore's own
#     derived-feature annotation: `sendingEnd.covered.representedInstance`),
#     not the InstanceRole itself (confirmed live: their returned id matched
#     the represented Entity's id, not the InstanceRole's).
#
#   - The export-time NPE (`Cannot invoke ISequenceEvent.getVerticalRange()
#     because "from" is null`) is Sirius's own internal vertical-ordering
#     computation for sequence diagrams (EventEnd/Lifeline bookkeeping,
#     a completely different mechanism from every other diagram type in
#     this module's x/y notation:Bounds). THE FIX: after creating
#     InstanceRole/Message nodes and edges via the same
#     DiagramServices.createNode()/createEdge() used throughout this
#     module, explicitly run Sirius's own ordering-repair chain that
#     normally fires automatically on interactive edits but doesn't survive
#     a raw-API-only creation:
#       1. org.eclipse.sirius.business.api.dialect.DialectManager.INSTANCE
#          .refresh(java_diag, monitor) -- materializes Sirius's
#          synchronized bordered "default execution" child node under each
#          InstanceRole's DNode (synchronizationLock="true" in the odesign),
#          which BasicMessageMapping's sourceMapping/targetMapping actually
#          point at, not InstanceRoleMapping itself (confirmed in oa.odesign).
#       2. org.eclipse.sirius.diagram.sequence.business.internal.refresh.
#          RefreshLayoutCommand(editingDomain, gmfDiagram, true), executed
#          via editingDomain.getCommandStack().execute(cmd) -- a plain
#          org.eclipse.emf.transaction.RecordingCommand, headless-safe, no
#          UI dependency. Its constructor's 2nd argument is the GMF
#          notation Diagram, NOT the Sirius DDiagram itself (they're
#          different EMF models -- confirmed live via ClassCastException
#          when passing java_diag directly); resolve it via
#          org.eclipse.sirius.diagram.ui.business.api.view.SiriusGMFHelper
#          .getGmfDiagram(java_diag, session). Constructed via raw
#          java.lang.reflect.Constructor.newInstance() (bundle.loadClass()
#          for a real java.lang.Class, then Array.newInstance() for a real
#          Object[] -- both regular constructor calls and Python-list
#          arguments hit the same Py4J interface-typed-parameter overload
#          resolution failure seen elsewhere in this module).
#   Confirmed live: this whole sequence must run in a SEPARATE headless
#   process from the one that created the nodes/edges -- the bordered
#   "default execution" nodes above only materialize after a save+reopen
#   cycle (same "GMF notation Views only materialize after save+reopen"
#   constraint as every other multi-pass diagram function in this module),
#   so create_scenario_diagram() below is 2-pass: pass 1 creates the
#   representation + InstanceRole nodes only; pass 2 (reopen) creates
#   Message edges (now that border nodes exist to target) and runs the
#   ordering-repair chain once, right before the final save.
_SCENARIO_DIAGRAM_MAPPINGS = {
    "OES": {
        "diagram": "Operational Interaction Scenario",
        "instance_role_mapping": "Instancerole Mapping OA",
        "message_mapping": "Basic message mapping OA",
    },
    "OAS": {
        "diagram": "Activity Interaction Scenario",
        "instance_role_mapping": "InstanceRoleMaping AIS",  # sic, Capella's own typo
        "message_mapping": "Basic message mapping AIS",
    },
}


def create_scenario_diagram(
    model_path: str, scenario_id: str, scenario_kind: str = "OES", diagram_name: str | None = None
) -> dict:
    """Create a real Capella (Sirius) sequence/scenario diagram (OES
    "Operational Interaction Scenario" or OAS "Activity Interaction
    Scenario") for an existing Scenario and save the model.

    scenario_id must be an existing Scenario (build one, its InstanceRoles,
    and its SequenceMessages first via create_element -- see that
    function's docstring). scenario_kind selects which of the two OA-layer
    scenario diagram descriptions to use ("OES" or "OAS").
    """
    if scenario_kind not in _SCENARIO_DIAGRAM_MAPPINGS:
        raise BridgeError(f"unknown scenario_kind {scenario_kind!r}, expected 'OES' or 'OAS'")
    mapping_cfg = _SCENARIO_DIAGRAM_MAPPINGS[scenario_kind]
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)

    pass1_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            oa = se.get_operational_analysis()

            scenario = None
            for candidate in oa.get_all_contents() if hasattr(oa, "get_all_contents") else []:
                if _element_id(candidate) == {scenario_id!r}:
                    scenario = candidate
                    break
            if scenario is None:
                raise ValueError(f"scenario_id not found in oa layer: {scenario_id!r}")

            repDef = get_representation_definition_by_name(model.session, {mapping_cfg["diagram"]!r})
            if repDef is None:
                raise ValueError("representation definition not found: {mapping_cfg["diagram"]}")
            irMapping = get_representation_mapping_by_name(repDef, {mapping_cfg["instance_role_mapping"]!r})
            if irMapping is None:
                raise ValueError("mapping not found: {mapping_cfg["instance_role_mapping"]}")
            resolved_diagram_name = {diagram_name!r} or f"{{{mapping_cfg["diagram"]!r}}} - {{scenario.get_label()}}"

            model.start_transaction()
            try:
                java_diag = create_representation(model.session, scenario, repDef, resolved_diagram_name)
                if java_diag is None:
                    raise ValueError("createRepresentation returned None for {mapping_cfg["diagram"]}")

                diagram_services = org.polarsys.capella.core.sirius.analysis.DiagramServices.getDiagramServices()
                node_count = 0
                for ir in scenario.get_owned_instance_roles():
                    diagram_services.createNode(irMapping, ir.get_java_object(), java_diag, java_diag)
                    node_count += 1

                monitor = org.eclipse.core.runtime.NullProgressMonitor()
                org.eclipse.sirius.business.api.dialect.DialectManager.INSTANCE.refresh(java_diag, monitor)

                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            _write_result({{"diagram_name": resolved_diagram_name, "node_count": node_count}})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass1 = _run_script(pass1_body)
    resolved_diagram_name = pass1["diagram_name"]
    node_count = pass1["node_count"]

    pass2_body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            oa = se.get_operational_analysis()

            scenario = None
            for candidate in oa.get_all_contents() if hasattr(oa, "get_all_contents") else []:
                if _element_id(candidate) == {scenario_id!r}:
                    scenario = candidate
                    break
            if scenario is None:
                raise ValueError(f"scenario_id not found in oa layer: {scenario_id!r}")

            target = None
            for d in model.get_all_diagrams():
                if d.get_name() == {resolved_diagram_name!r}:
                    target = d
                    break
            if target is None:
                raise ValueError(f"diagram not found after pass 1: {resolved_diagram_name!r}")
            java_diag = target.get_java_object().getRepresentation()

            dnode_by_ir_id = {{}}
            for de in java_diag.getOwnedDiagramElements():
                if de.eClass().getName() == "DNode":
                    dnode_by_ir_id[de.getTarget().getId()] = de

            repDef = get_representation_definition_by_name(model.session, {mapping_cfg["diagram"]!r})
            msgMapping = get_representation_mapping_by_name(repDef, {mapping_cfg["message_mapping"]!r})
            if msgMapping is None:
                raise ValueError("mapping not found: {mapping_cfg["message_mapping"]}")
            diagram_services = org.polarsys.capella.core.sirius.analysis.DiagramServices.getDiagramServices()

            model.start_transaction()
            try:
                edge_count = 0
                for msg in scenario.get_owned_messages():
                    # NOTE: msg.get_sending_instance_role()/
                    # get_receiving_instance_role() do NOT return the
                    # InstanceRole despite the name -- they resolve all the
                    # way through to the represented Entity/Part (matches
                    # Interaction.ecore's own derived-feature annotation:
                    # `sendingEnd.covered.representedInstance`). The real
                    # InstanceRole is MessageEnd.coveredInstanceRoles
                    # (confirmed live) -- read it via raw EMF instead.
                    send_end = msg.get_java_object().getSendingEnd()
                    recv_end = msg.get_java_object().getReceivingEnd()
                    if send_end is None or recv_end is None:
                        continue
                    send_covered = send_end.getCoveredInstanceRoles()
                    recv_covered = recv_end.getCoveredInstanceRoles()
                    if send_covered.size() == 0 or recv_covered.size() == 0:
                        continue
                    src_dnode = dnode_by_ir_id.get(send_covered.get(0).getId())
                    tgt_dnode = dnode_by_ir_id.get(recv_covered.get(0).getId())
                    if src_dnode is None or tgt_dnode is None:
                        continue
                    src_border = src_dnode.getOwnedBorderedNodes()
                    tgt_border = tgt_dnode.getOwnedBorderedNodes()
                    src_node = src_border.get(0) if src_border.size() > 0 else src_dnode
                    tgt_node = tgt_border.get(0) if tgt_border.size() > 0 else tgt_dnode
                    diagram_services.createEdge(msgMapping, src_node, tgt_node, msg.get_java_object())
                    edge_count += 1

                monitor = org.eclipse.core.runtime.NullProgressMonitor()
                org.eclipse.sirius.business.api.dialect.DialectManager.INSTANCE.refresh(java_diag, monitor)

                editing_domain = model.session.getTransactionalEditingDomain()
                sequence_bundle = org.eclipse.core.runtime.Platform.getBundle("org.eclipse.sirius.diagram.sequence")
                RefreshLayoutCommandClass = sequence_bundle.loadClass(
                    "org.eclipse.sirius.diagram.sequence.business.internal.refresh.RefreshLayoutCommand")
                ctor = None
                for candidate in RefreshLayoutCommandClass.getDeclaredConstructors():
                    if candidate.getParameterCount() == 3:
                        ctor = candidate
                        break
                ctor.setAccessible(True)
                gmf_diagram = org.eclipse.sirius.diagram.ui.business.api.view.SiriusGMFHelper.getGmfDiagram(java_diag, model.session)
                object_class = sequence_bundle.loadClass("java.lang.Object")
                ctor_args = java.lang.reflect.Array.newInstance(object_class, 3)
                java.lang.reflect.Array.set(ctor_args, 0, editing_domain)
                java.lang.reflect.Array.set(ctor_args, 1, gmf_diagram)
                java.lang.reflect.Array.set(ctor_args, 2, java.lang.Boolean(True))
                refresh_cmd = ctor.newInstance(ctor_args)
                editing_domain.getCommandStack().execute(refresh_cmd)

                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()

            _write_result({{
                "diagram_uid": target.get_uid(),
                "diagram_name": target.get_name(),
                "edge_count": edge_count,
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    pass2 = _run_script(pass2_body)
    return {
        "diagram_uid": pass2["diagram_uid"],
        "diagram_name": pass2["diagram_name"],
        "node_count": node_count,
        "edge_count": pass2["edge_count"],
    }


def list_diagrams(model_path: str) -> dict:
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            diagrams = model.get_all_diagrams()
            _write_result({{
                "diagrams": [
                    {{"uid": d.get_uid(), "name": d.get_name(), "type": d.get_type()}}
                    for d in diagrams
                ]
            }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def get_diagram(model_path: str, diagram_uid: str) -> dict:
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            target_uid = {diagram_uid!r}
            found = None
            for d in model.get_all_diagrams():
                if d.get_uid() == target_uid:
                    found = d
                    break
            if found is None:
                _write_result({{"error": f"diagram not found: {{target_uid}}"}})
            else:
                target = found.get_target()
                _write_result({{
                    "uid": found.get_uid(),
                    "name": found.get_name(),
                    "type": found.get_type(),
                    "target_id": target.get_id() if target is not None else None,
                    "target_label": found.get_target().get_label() if target is not None else None,
                }})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def delete_diagram(model_path: str, diagram_uid: str) -> dict:
    """Delete a diagram (DRepresentation + its DRepresentationDescriptor) and
    save the model. Uses Sirius's own DialectManager.deleteRepresentation --
    same class python4capella's create_representation (diagram.py) calls for
    creation, just the inverse operation; not wrapped by simplified_api
    itself so referenced directly via its fully-qualified Java name, same
    style diagram.py itself uses for org.eclipse.core.runtime.NullProgressMonitor.
    """
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            target_uid = {diagram_uid!r}
            found = None
            for d in model.get_all_diagrams():
                if d.get_uid() == target_uid:
                    found = d
                    break
            if found is None:
                _write_result({{"error": f"diagram not found: {{target_uid}}"}})
            else:
                deleted_name = found.get_name()
                descriptor = found.get_java_object()
                model.start_transaction()
                try:
                    org.eclipse.sirius.business.api.dialect.DialectManager.INSTANCE.deleteRepresentation(descriptor, model.session)
                    model.commit_transaction()
                except Exception:
                    model.rollback_transaction()
                    raise
                model.save()
                _write_result({{"deleted": True, "uid": target_uid, "name": deleted_name}})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def layout_diagram(model_path: str, diagram_uid: str) -> dict:
    """Apply Capella's native 'Layout > All' to rearrange all elements in an
    existing diagram.  Uses GMF's OffscreenEditPartFactory + ArrangeRequest
    (same code path as the UI 'Layout > All' menu action), so it respects
    pinned elements, registered layout providers, and all Sirius layout rules.

    Works for all diagram types: breakdown, container, class, capability, and
    scenario diagrams.  Falls back to ``_layout_tree`` when the Sirius/GMF
    layout pipeline is unavailable (e.g. a mapping without a registered
    LayoutProvider).

    Returns ``{"success": True, "diagram_uid": ..., "diagram_name": ...}`` on
    success.  On failure, raises BridgeError with a descriptive message --
    never crashes the server.
    """
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = _diagram_include() + textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            target_uid = {diagram_uid!r}

            # --- find diagram by UID ---
            found = None
            for d in model.get_all_diagrams():
                if d.get_uid() == target_uid:
                    found = d
                    break
            if found is None:
                _write_result({{"error": f"diagram not found: {{target_uid}}"}})
            else:
                java_diag = found.get_java_object().getRepresentation()
                diagram_name = found.get_name()

                # --- resolve GMF notation Diagram (NOT the Sirius DDiagram) ---
                gmf_diagram = org.eclipse.sirius.diagram.ui.business.api.view.\\
                    SiriusGMFHelper.getGmfDiagram(java_diag, model.session)
                if gmf_diagram is None:
                    _write_result({{"error": "could not resolve GMF notation diagram for this representation"}})
                else:
                    # --- load classes via OSGi bundle reflection ---
                    # (same pattern as RefreshLayoutCommand in create_scenario_diagram)
                    ui_bundle = org.eclipse.core.runtime.Platform.getBundle(
                        "org.eclipse.gmf.runtime.diagram.ui")
                    FactoryCls = ui_bundle.loadClass(
                        "org.eclipse.gmf.runtime.diagram.ui.OffscreenEditPartFactory")
                    DisplayCls = ui_bundle.loadClass(
                        "org.eclipse.swt.widgets.Display")
                    ShellCls = ui_bundle.loadClass(
                        "org.eclipse.swt.widgets.Shell")
                    RequestCls = ui_bundle.loadClass(
                        "org.eclipse.gmf.runtime.diagram.ui.requests.ArrangeRequest")

                    # --- OffscreenEditPartFactory singleton ---
                    factory = FactoryCls.getMethod("getInstance", None).invoke(None, None)

                    # --- SWT Display + hidden Shell (Xvfb provides the Display) ---
                    display = DisplayCls.getMethod("getDefault", None).invoke(None, None)
                    shell_ctor = None
                    for c in ShellCls.getDeclaredConstructors():
                        if c.getParameterCount() == 1:
                            shell_ctor = c
                            break
                    if shell_ctor is None:
                        _write_result({{"error": "cannot find Shell(Display) constructor"}})
                    else:
                        shell_ctor.setAccessible(True)
                        shell = shell_ctor.newInstance([display])

                        # --- create DiagramEditPart offscreen ---
                        create_m = FactoryCls.getMethod(
                            "createDiagramEditPart",
                            [gmf_diagram.getClass(), ShellCls])
                        diagram_ep = create_m.invoke(factory, [gmf_diagram, shell])

                        if diagram_ep is None:
                            _write_result({{"error": "OffscreenEditPartFactory returned null -- diagram may not support headless layout"}})
                        else:
                            # --- ArrangeRequest("ACTION_ARRANGE_ALL") ---
                            # 1-arg ctor: layoutType defaults to null (= DEFAULT)
                            req_ctor = None
                            for c in RequestCls.getDeclaredConstructors():
                                if c.getParameterCount() == 1:
                                    req_ctor = c
                                    break
                            if req_ctor is None:
                                _write_result({{"error": "cannot find ArrangeRequest constructor"}})
                            else:
                                req_ctor.setAccessible(True)
                                request = req_ctor.newInstance(["ACTION_ARRANGE_ALL"])

                                parts = java.util.ArrayList()
                                parts.add(diagram_ep)
                                request.setPartsToArrange(parts)

                                cmd = diagram_ep.getCommand(request)
                                if cmd is not None and cmd.canExecute():
                                    cmd.execute()
                                    model.save()
                                    _write_result({{
                                        "success": True,
                                        "diagram_uid": target_uid,
                                        "diagram_name": diagram_name,
                                    }})
                                else:
                                    _write_result({{
                                        "error": "layout command could not be executed for this diagram",
                                        "diagram_uid": target_uid,
                                        "diagram_name": diagram_name,
                                    }})

        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def export_diagram(model_path: str, image_format: str = "PNG") -> dict:
    """Export every diagram in the model to image files via Capella's own
    native headless app (not python4capella's own export_as_image, which
    is flagged status: KO/untested in the addon itself). Separate `-appid`
    from the EASE/python4capella one used by _run_script -- no script or
    Python4Capella project import needed here.

    The output folder is always a `_diagram_exports` sibling of the model
    file, inside MODELS_ROOT: `-outputfolder` is resolved by Capella's
    commandline core as an Eclipse *resource* path (like `-input`), not a
    raw filesystem path -- it must live inside a registered project, or
    "Cannot create outputfolder!" fails with a ResourceException.
    """
    abs_path = resolve_model_path(model_path)
    out_dir = abs_path.parent / f"{abs_path.stem}_diagram_exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    models_project = _ensure_models_project()
    workspace_path = _workspace_path_for_model(abs_path)
    output_workspace_path = _workspace_path_for_model(out_dir)
    # Unlike the `workspace:/project/script.py` script-path argument used
    # by _run_script, -input/-outputfolder take the resource path AS-IS
    # (leading single slash, "/project/path" -- confirmed against
    # Capella's own commandline docs), not the EASE workspace:// URL form.
    input_arg = workspace_path
    output_arg = output_workspace_path

    call_dir = WORKSPACE_ROOT / uuid.uuid4().hex
    call_dir.mkdir(parents=True)
    cmd = [
        "xvfb-run", "-a", CAPELLA_BIN,
        "-nosplash", "-consolelog",
        "-application", "org.polarsys.capella.core.commandline.core",
        "-appid", "org.polarsys.capella.exportRepresentations",
        "-data", str(call_dir),
        "-import", str(models_project),
        "-input", input_arg,
        "-outputfolder", output_arg,
        "-imageFormat", image_format,
    ]
    try:
        proc = _spawn_and_wait(cmd, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"Capella diagram export timed out after {DEFAULT_TIMEOUT}s") from exc
    # KNOWN BUG, fixed 2026-08-15: the real exporter writes to a NESTED
    # subdirectory (<out_dir>/<eclipse_project_name>/<model_filename>.aird/
    # *.png), not directly under out_dir -- a non-recursive glob() here
    # always returned files: [] even on a fully successful export. rglob()
    # walks the whole tree instead.
    exported = sorted(str(p) for p in out_dir.rglob(f"*.{image_format.lower()}"))
    if proc.returncode != 0 and not exported:
        raise BridgeError(
            f"Capella diagram export failed (exit={proc.returncode}). stderr tail: {proc.stderr[-2000:]}"
        )
    return {"output_dir": str(out_dir), "files": exported}
