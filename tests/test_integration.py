"""Integration tests against a real python4capella instance.

Skipped automatically unless CAPELLA_BIN points at an actual Capella
install and a fixture model is present at tests/fixtures/demo.aird. Run
via one of:

    scripts/run_integration_tests.sh        # builds the Docker image, runs inside a container
    scripts/run_integration_tests_local.sh  # runs directly on the host against a local Capella install

either way so CAPELLA_BIN/MODELS_ROOT resolve for real. See
docs/decisions/0002-headless-por-chamada.md and the "Atualização" note in
docs/decisions/0003-empacotamento-docker.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capella_mcp import bridge

_capella_missing = not Path(bridge.CAPELLA_BIN).exists()
_fixture_missing = not (bridge.MODELS_ROOT / "demo.aird").exists()
_car_hmi_missing = not (bridge.MODELS_ROOT / "car_hmi" / "car_hmi.aird").exists()

skip_reason = (
    "Capella not available at CAPELLA_BIN — run via scripts/run_integration_tests.sh"
    if _capella_missing
    else "tests/fixtures/demo.aird missing — see tests/fixtures/README.md"
    if _fixture_missing
    else None
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(skip_reason is not None, reason=skip_reason or ""),
]


def test_list_layers_returns_five_arcadia_layers():
    result = bridge.list_layers("demo.aird")
    layers = {entry["layer"] for entry in result["layers"]}
    assert layers == {"oa", "sa", "la", "pa", "epbs"}


def test_create_then_get_element_round_trips():
    created = bridge.create_element(
        "demo.aird", "la", "LogicalComponent", "Integration Test Component",
    )
    assert created["id"] is not None

    fetched = bridge.get_element("demo.aird", created["id"])
    assert fetched["id"] == created["id"]
    assert fetched["label"] == "Integration Test Component"


def test_update_element_persists_across_separate_calls():
    created = bridge.create_element(
        "demo.aird", "la", "LogicalComponent", "Before Update",
    )
    bridge.update_element("demo.aird", created["id"], {"name": "After Update"})

    # separate call, separate Capella process -- proves the save() actually persisted
    fetched = bridge.get_element("demo.aird", created["id"])
    assert fetched["label"] == "After Update"


@pytest.mark.skipif(_car_hmi_missing, reason="tests/fixtures/car_hmi/car_hmi.aird missing")
class TestDiagrams:
    """Round-trips against car_hmi.aird (has real OA entities/activities,
    unlike the plain demo.aird). Every created diagram is deleted again at
    the end via delete_diagram, so repeated local runs don't accumulate
    diagrams in the committed fixture."""

    def test_create_diagram_breakdown_renders_non_blank_png(self):
        """OABD (Operational Activity Breakdown) is the one NodeMapping
        breakdown diagram type known to render correctly headless -- this
        is the regression guard for that."""
        # car_hmi.aird has more than one root-level OperationalActivity
        # (leftover from earlier manual sessions), so type_name alone is
        # ambiguous here -- resolve root_id explicitly like a real caller
        # would after getting create_diagram's "multiple root-level
        # elements found" error.
        # "Root Operational Activity" is childless in this fixture -- use
        # the other root-level activity, which owns 2 sub-activities, so
        # the diagram actually has nodes to render.
        activities = bridge.list_elements("car_hmi/car_hmi.aird", "oa", type_filter="OperationalActivity")
        root = next(e for e in activities["elements"] if e["label"] == "Exibir velocidade do veículo")
        created = bridge.create_diagram("car_hmi/car_hmi.aird", "oa", root_id=root["id"])
        try:
            assert created["node_count"] > 0
            export = bridge.export_diagram("car_hmi/car_hmi.aird")
            match = [f for f in export["files"] if created["diagram_name"] in f]
            assert match, f"exported PNG not found for {created['diagram_name']!r} in {export['files']}"
            # ~125 bytes is the known "blank" size (see CONTAINER_DIAGRAMS'
            # comment in bridge.py); a real rendered diagram is well above it.
            assert Path(match[0]).stat().st_size > 300
        finally:
            bridge.delete_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])

    def test_create_container_diagram_renders_non_blank_png(self):
        """Operational Entity Blank -- regression guard for the
        DiagramServices.createContainer() fix (see CONTAINER_DIAGRAMS'
        comment in bridge.py). Used to always export blank; now must
        render a real PNG same as any other diagram type."""
        created = bridge.create_container_diagram("car_hmi/car_hmi.aird", "oa", "OperationalEntity")
        try:
            assert created["node_count"] > 0

            fetched = bridge.get_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])
            assert fetched["uid"] == created["diagram_uid"]
            assert fetched["type"] == "Operational Entity Blank"

            export = bridge.export_diagram("car_hmi/car_hmi.aird")
            match = [f for f in export["files"] if created["diagram_name"] in f]
            assert match, f"exported PNG not found for {created['diagram_name']!r} in {export['files']}"
            assert Path(match[0]).stat().st_size > 300
        finally:
            bridge.delete_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])

    def test_create_container_diagram_unknown_combo_raises(self):
        with pytest.raises(bridge.BridgeError, match="no container/blank diagram known"):
            bridge.create_container_diagram("car_hmi/car_hmi.aird", "la", "LogicalComponent")

    def test_oaib_renders_non_blank_png(self):
        """OAIB is CONTAINER_DIAGRAMS' only entry with an edge_mapping --
        this also regression-guards the create_representation target fix
        (roots[0] instead of the package, which OAIB's repDef rejects
        silently -- see the comment in bridge.py's create_container_diagram)
        and the DiagramServices.createContainer() render fix."""
        created = bridge.create_container_diagram("car_hmi/car_hmi.aird", "oa", "OperationalActivity")
        try:
            assert created["node_count"] > 0

            fetched = bridge.get_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])
            assert fetched["uid"] == created["diagram_uid"]
            assert fetched["type"] == "Operational Activity Interaction Blank"

            export = bridge.export_diagram("car_hmi/car_hmi.aird")
            match = [f for f in export["files"] if created["diagram_name"] in f]
            assert match, f"exported PNG not found for {created['diagram_name']!r} in {export['files']}"
            assert Path(match[0]).stat().st_size > 300
        finally:
            bridge.delete_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])

    def test_create_class_diagram_renders_non_blank_png(self):
        """CDB, rooted at the OA layer's default DataPkg. Same
        DiagramServices.createContainer() fix as create_container_diagram
        (both DT_DataPkg/DT_Class are ContainerMappings)."""
        created = bridge.create_class_diagram("car_hmi/car_hmi.aird", "oa")
        try:
            assert created["node_count"] >= 1

            fetched = bridge.get_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])
            assert fetched["uid"] == created["diagram_uid"]
            assert fetched["type"] == "Class Diagram Blank"

            export = bridge.export_diagram("car_hmi/car_hmi.aird")
            match = [f for f in export["files"] if created["diagram_name"] in f]
            assert match, f"exported PNG not found for {created['diagram_name']!r} in {export['files']}"
            # CDB's single-empty-package case is a small but real render
            # (icon + border + label), well above the ~125-byte blank size.
            assert Path(match[0]).stat().st_size > 300
        finally:
            bridge.delete_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])

    def test_create_class_diagram_unknown_layer_raises(self):
        with pytest.raises(bridge.BridgeError, match="unknown layer"):
            bridge.create_class_diagram("car_hmi/car_hmi.aird", "not-a-layer")

    def test_create_capability_diagram_renders_non_blank_png(self):
        """OCB, rooted at OperationalCapabilityPkg -- the one diagram
        function in this module whose target isn't the entity/EntityPkg
        every other one uses (regression guard for that), plus the same
        DiagramServices.createContainer() render fix as the others."""
        created = bridge.create_capability_diagram("car_hmi/car_hmi.aird")
        try:
            assert created["node_count"] > 0

            fetched = bridge.get_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])
            assert fetched["uid"] == created["diagram_uid"]
            assert fetched["type"] == "Operational Capabilities Blank"

            export = bridge.export_diagram("car_hmi/car_hmi.aird")
            match = [f for f in export["files"] if created["diagram_name"] in f]
            assert match, f"exported PNG not found for {created['diagram_name']!r} in {export['files']}"
            assert Path(match[0]).stat().st_size > 300
        finally:
            bridge.delete_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])

    def test_mode_state_machine_renders_non_blank_png(self):
        """("la", "Region") is NodeMapping (MSM_ModeState), not the
        ContainerMapping "Blank" family -- confirmed live to render
        correctly, unlike create_container_diagram's entries. Builds the
        whole Component -> StateMachine -> Region -> 2 States chain via
        create_element first (elements are left in place afterwards,
        matching this file's existing convention for demo.aird)."""
        comp = bridge.create_element("car_hmi/car_hmi.aird", "la", "LogicalComponent", "MSM Test Component")
        sm = bridge.create_element("car_hmi/car_hmi.aird", "la", "StateMachine", "SM1", parent_id=comp["id"])
        region = bridge.create_element("car_hmi/car_hmi.aird", "la", "Region", "Region1", parent_id=sm["id"])
        bridge.create_element("car_hmi/car_hmi.aird", "la", "State", "Idle", parent_id=region["id"])
        bridge.create_element("car_hmi/car_hmi.aird", "la", "State", "Running", parent_id=region["id"])

        created = bridge.create_diagram("car_hmi/car_hmi.aird", "la", root_id=region["id"])
        try:
            assert created["node_count"] == 2
            export = bridge.export_diagram("car_hmi/car_hmi.aird")
            match = [f for f in export["files"] if created["diagram_name"] in f]
            assert match, f"exported PNG not found for {created['diagram_name']!r} in {export['files']}"
            assert Path(match[0]).stat().st_size > 300
        finally:
            bridge.delete_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])
