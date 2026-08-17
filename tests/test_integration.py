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

import textwrap
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
def test_create_functional_exchange_round_trips():
    """Regression guard: FunctionalExchange used to silently not persist --
    create_element fell through to the broken get_contents().append(el)
    fallback (a disconnected Python-side snapshot, not a live EMF
    collection), so a follow-up get_element always reported "element not
    found" even though create_element itself reported fake success. Fixed
    by routing through Function.get_outputs()/.get_inputs() (real
    containment, ports) + set_source_port()/set_target_port() + the owning
    Function's get_owned_functional_exchanges() (containment for the
    exchange itself)."""
    activities = bridge.list_elements("car_hmi/car_hmi.aird", "oa", type_filter="OperationalActivity")
    by_label = {e["label"]: e["id"] for e in activities["elements"]}
    parent_id = by_label["Exibir velocidade do veículo"]
    src_id = by_label["Monitorar velocidade do veículo"]
    tgt_id = by_label["Fornecer velocidade do veículo"]

    created = bridge.create_element(
        "car_hmi/car_hmi.aird", "oa", "FunctionalExchange", "Velocidade Monitorada",
        parent_id=parent_id,
        attributes={"source_id": src_id, "target_id": tgt_id},
    )
    assert created["id"] is not None

    # separate call, separate Capella process -- proves save() actually persisted
    fetched = bridge.get_element("car_hmi/car_hmi.aird", created["id"])
    assert fetched["id"] == created["id"]
    assert fetched["label"] == "Velocidade Monitorada"
    assert fetched["type"] == "FunctionalExchange"


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
        """OCB with only entities/actors involved (no OperationalCapability
        data in car_hmi.aird by default) -- rooted at OperationalCapabilityPkg,
        the one diagram function in this module whose target isn't the
        entity/EntityPkg every other one uses (regression guard for that),
        plus the same DiagramServices.createContainer() render fix as the
        others. See test_create_capability_diagram_with_involvement_renders_
        edges below for the free-node/edge shape this diagram actually
        needs real capability data to exercise."""
        created = bridge.create_capability_diagram("car_hmi/car_hmi.aird")
        try:
            assert created["node_count"] > 0
            assert created["edge_count"] == 0

            fetched = bridge.get_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])
            assert fetched["uid"] == created["diagram_uid"]
            assert fetched["type"] == "Operational Capabilities Blank"

            export = bridge.export_diagram("car_hmi/car_hmi.aird")
            match = [f for f in export["files"] if created["diagram_name"] in f]
            assert match, f"exported PNG not found for {created['diagram_name']!r} in {export['files']}"
            assert Path(match[0]).stat().st_size > 300
        finally:
            bridge.delete_diagram("car_hmi/car_hmi.aird", created["diagram_uid"])

    def test_create_capability_diagram_with_involvement_renders_edges(self):
        """Regression guard for the OCB shape bug: the first version of
        create_capability_diagram nested capabilities inside entity
        containers, which doesn't match oa.odesign's real definition (free
        node + involvement edge) -- car_hmi.aird has no
        OperationalCapability/involvement data by default, so that bug was
        never actually exercised by this project's own tests. This builds
        real EntityOperationalCapabilityInvolvement data (python4capella has
        no wrapper for it -- raw EMF via create_e_object_from_e_classifier,
        same pattern bridge.py itself uses for anything unwrapped) and
        checks the diagram actually gets edges, not just entity containers."""
        abs_path = bridge.resolve_model_path("car_hmi/car_hmi.aird")
        workspace_path = bridge._workspace_path_for_model(abs_path)
        body = bridge._diagram_include() + textwrap.dedent(f"""\
            try:
                model = CapellaModel()
                model.open({workspace_path!r})
                se = model.get_system_engineering()
                oa = se.get_operational_analysis()
                entity_pkg = oa.get_entity_pkg()
                cap_pkg = oa.get_operational_capability_pkg()
                entities = {{e.get_label(): e for e in entity_pkg.get_owned_entities()}}
                veiculo = entities["Veículo"]
                motorista = entities["Motorista"]
                inv_class = get_e_classifier("http://www.polarsys.org/capella/core/oa/" + capella_version(), "EntityOperationalCapabilityInvolvement")
                model.start_transaction()
                try:
                    cap = OperationalCapability()
                    cap.set_name("Integration Test Capability")
                    cap_pkg.get_owned_operational_capabilities().add(cap)
                    for entity in (veiculo, motorista):
                        involvement = create_e_object_from_e_classifier(inv_class)
                        involvement.setInvolved(entity.get_java_object())
                        cap.get_java_object().getOwnedEntityOperationalCapabilityInvolvements().add(involvement)
                    model.commit_transaction()
                except Exception:
                    model.rollback_transaction()
                    raise
                model.save()
                _write_result({{"ok": True}})
            except Exception as exc:
                _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
            """)
        bridge._run_script(body)

        created = bridge.create_capability_diagram("car_hmi/car_hmi.aird")
        try:
            # 2 involvements (Veículo, Motorista) both pointing at the same
            # capability -- must be deduped to one node, two edges.
            assert created["edge_count"] == 2

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
