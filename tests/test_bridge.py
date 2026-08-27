"""Unit tests for the headless bridge -- subprocess is mocked, no Capella
or Docker required. Integration tests (tests/test_integration.py) cover the
real python4capella API against a built image."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from capella_mcp import bridge


@pytest.fixture
def models_root(tmp_path, monkeypatch):
    root = tmp_path / "models"
    root.mkdir()
    (root / "demo.aird").write_text("<fake/>")
    monkeypatch.setattr(bridge, "MODELS_ROOT", root.resolve())
    return root


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    monkeypatch.setattr(bridge, "WORKSPACE_ROOT", ws)
    return ws


@pytest.fixture(autouse=True)
def _stub_python4capella_project(tmp_path, monkeypatch):
    """_run_script() always calls _ensure_python4capella_project(), which
    normally extracts a project from the real plugin jar -- stub it out so
    unit tests never touch a real Capella install, same as subprocess.run."""
    fake = tmp_path / "Python4Capella"
    fake.mkdir()
    (fake / ".project").write_text("<fake/>")
    monkeypatch.setattr(bridge, "_python4capella_project_dir", fake)


def _run_writing(result_data: dict):
    """Fake subprocess.run that mimics a Capella process writing result.json."""

    def _run(cmd, capture_output, text, timeout):
        call_dir = Path(cmd[cmd.index("-data") + 1])
        (call_dir / "result.json").write_text(json.dumps(result_data))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


class TestResolveModelPath:
    def test_valid_path_resolves(self, models_root):
        assert bridge.resolve_model_path("demo.aird") == (models_root / "demo.aird").resolve()

    def test_missing_model_raises(self, models_root):
        with pytest.raises(bridge.BridgeError, match="model not found"):
            bridge.resolve_model_path("missing.aird")

    def test_escape_raises(self, models_root):
        with pytest.raises(bridge.BridgeError, match="escapes models root"):
            bridge.resolve_model_path("../outside.aird")


class TestRunScriptProtocol:
    def test_list_layers_parses_result(self, models_root, workspace_root, monkeypatch):
        expected = {"layers": [{"layer": "oa", "present": True}]}
        monkeypatch.setattr(bridge, "_spawn_and_wait", _run_writing(expected))
        assert bridge.list_layers("demo.aird") == expected

    def test_error_in_result_raises_bridge_error(self, models_root, workspace_root, monkeypatch):
        monkeypatch.setattr(
            bridge, "_spawn_and_wait", _run_writing({"error": "boom", "traceback": "tb"})
        )
        with pytest.raises(bridge.BridgeError, match="boom"):
            bridge.list_layers("demo.aird")

    def test_missing_result_file_raises(self, models_root, workspace_root, monkeypatch):
        def _run(cmd, capture_output, text, timeout):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="something crashed")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        with pytest.raises(bridge.BridgeError, match="produced no result"):
            bridge.list_layers("demo.aird")

    def test_timeout_raises_bridge_error(self, models_root, workspace_root, monkeypatch):
        def _run(cmd, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        with pytest.raises(bridge.BridgeError, match="timed out"):
            bridge.list_layers("demo.aird")

    def test_unknown_layer_rejected_before_subprocess(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for an invalid layer")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        with pytest.raises(bridge.BridgeError, match="unknown layer"):
            bridge.list_elements("demo.aird", "not-a-layer")


class TestGeneratedScripts:
    """Every operation's generated script must at least be valid, compilable
    Python -- this is what would actually run inside the Capella process."""

    def _capture_and_compile(self, monkeypatch, result_data=None):
        captured = {}

        def _run(cmd, capture_output, text, timeout):
            call_dir = Path(cmd[cmd.index("-data") + 1])
            script = (call_dir / bridge._SCRIPT_PROJECT_NAME / "script.py").read_text()
            captured["script"] = script
            compile(script, "<script>", "exec")
            (call_dir / "result.json").write_text(json.dumps(result_data or {"ok": True}))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        return captured

    def test_list_elements_with_type_filter(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch)
        bridge.list_elements("demo.aird", "la", type_filter="LogicalComponent")
        assert "LogicalComponent" in captured["script"]

    def test_get_element(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch)
        bridge.get_element("demo.aird", "some-id")
        assert "some-id" in captured["script"]

    def test_create_element(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch)
        bridge.create_element(
            "demo.aird", "la", "LogicalComponent", "Foo",
            parent_id=None, attributes={"description": "bar"},
        )
        assert "LogicalComponent" in captured["script"]
        assert "start_transaction" in captured["script"]
        # save() must be guarded so the exact same body text is safe to
        # reuse unmodified for an attach-mode write -- see AttachMode's
        # "no forced save" tests below and docs/decisions/0006.
        assert "if not _ATTACH_MODE:" in captured["script"]

    def test_update_element(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch)
        bridge.update_element("demo.aird", "some-id", {"description": "baz"})
        assert "start_transaction" in captured["script"]
        assert "if not _ATTACH_MODE:" in captured["script"]

    def test_list_diagrams(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch, {"diagrams": []})
        bridge.list_diagrams("demo.aird")
        assert "get_all_diagrams" in captured["script"]

    def test_get_diagram(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(
            monkeypatch,
            {"uid": "u1", "name": "N", "type": "T", "target_id": "t1", "target_label": "L"},
        )
        bridge.get_diagram("demo.aird", "u1")
        assert "u1" in captured["script"]

    def test_create_element_data_pkg_and_class(self, models_root, workspace_root, monkeypatch):
        for type_name, expected_call in [
            ("DataPkg", "get_owned_data_pkgs"),
            ("Class", "get_owned_classes"),
        ]:
            captured = self._capture_and_compile(monkeypatch)
            bridge.create_element("demo.aird", "la", type_name, "X", parent_id="parent-id")
            assert expected_call in captured["script"]

    def test_create_element_state_machine_chain(self, models_root, workspace_root, monkeypatch):
        """StateMachine/Region/State/Mode all require parent_id and route
        through their own accessor -- one compile check per type is enough
        to catch a typo in the accessor name."""
        for type_name, expected_call in [
            ("StateMachine", "get_owned_state_machines"),
            ("Region", "get_owned_regions"),
            ("State", "get_owned_states"),
            ("Mode", "get_owned_states"),
        ]:
            captured = self._capture_and_compile(monkeypatch)
            bridge.create_element("demo.aird", "la", type_name, "X", parent_id="parent-id")
            assert expected_call in captured["script"]

    def test_create_element_functional_exchange(self, models_root, workspace_root, monkeypatch):
        """Verified live against a real model: ports via Function.get_outputs()/
        .get_inputs() (real containment on Activity.ecore, not the
        FunctionSpecification-only ownedFunctionPorts), exchange wiring via
        set_source_port()/set_target_port(), containment via
        get_owned_functional_exchanges() on the parent_id Function."""
        captured = self._capture_and_compile(monkeypatch)
        bridge.create_element(
            "demo.aird", "oa", "FunctionalExchange", "X",
            parent_id="parent-id",
            attributes={"source_id": "src-id", "target_id": "tgt-id"},
        )
        script = captured["script"]
        assert "get_owned_functional_exchanges" in script
        assert "get_outputs" in script
        assert "get_inputs" in script
        assert "set_source_port" in script
        assert "set_target_port" in script

    def test_create_element_scenario(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch)
        bridge.create_element("demo.aird", "oa", "Scenario", "X", parent_id="cap-id")
        assert "get_owned_scenarios" in captured["script"]

    def test_create_element_instance_role(self, models_root, workspace_root, monkeypatch):
        """Verified live: representedInstance wants an AbstractInstance, not
        the represented Entity/Function directly -- Entity needs an
        indirection through its own self-representing Part
        (CapellaServices.creationService()+getAbstractTypedElements()),
        while AbstractFunction (Operational/System/LogicalFunction) directly
        implements AbstractInstance and takes the direct eSet(). The
        generated script always includes both code paths (the direct eSet()
        attempted first, the Part-indirection as its except fallback) --
        which one actually runs depends on the represented element's real
        Java type at Capella runtime, not at script-generation time.

        Also a regression guard for a real OES lifeline-header bug (found
        2026-08-17): creationService() auto-creates the Part with a
        placeholder name ("OA 2") and, once that Part exists, even the
        Entity's OWN getName()/get_label() reads back through it -- so the
        represented Entity's real name must be captured BEFORE
        creationService() runs, not re-read after (which would silently
        capture the already-corrupted placeholder)."""
        captured = self._capture_and_compile(monkeypatch)
        bridge.create_element(
            "demo.aird", "oa", "InstanceRole", "X",
            parent_id="scenario-id",
            attributes={"represented_instance_id": "entity-id"},
        )
        script = captured["script"]
        assert "get_owned_instance_roles" in script
        assert "representedInstance" in script
        assert "CapellaServices" in script
        assert "getAbstractTypedElements" in script
        assert "part.setName" in script
        # regression guard for the exact bug: represented_name must be read
        # BEFORE creationService() is called, not after
        represented_name_idx = script.index("represented_name = represented.get_java_object().getName()")
        creation_service_idx = script.index("capella_services.creationService(")
        assert represented_name_idx < creation_service_idx

    def test_create_element_sequence_message(self, models_root, workspace_root, monkeypatch):
        """Verified live: no capella.py wrapper for MessageEnd/
        EventSentOperation/EventReceiptOperation -- built via EMF_API.py's
        create_e_object(), same primitive capella.py's own constructors use
        internally."""
        captured = self._capture_and_compile(monkeypatch)
        bridge.create_element(
            "demo.aird", "oa", "SequenceMessage", "X",
            parent_id="scenario-id",
            attributes={"source_id": "ir-a-id", "target_id": "ir-b-id"},
        )
        script = captured["script"]
        assert "get_owned_messages" in script
        assert "getOwnedInteractionFragments" in script
        assert "getOwnedEvents" in script
        assert "EventSentOperation" in script
        assert "EventReceiptOperation" in script
        assert "setSendingEnd" in script
        assert "setReceivingEnd" in script

    def test_delete_diagram(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(
            monkeypatch, {"deleted": True, "uid": "u1", "name": "N"}
        )
        bridge.delete_diagram("demo.aird", "u1")
        assert "DialectManager" in captured["script"]
        assert "deleteRepresentation" in captured["script"]
        assert "start_transaction" in captured["script"]


class TestHybridReadDispatcher:
    """list_layers/list_elements/get_element try fast_reader first (see
    docs/decisions/0005-camada-leitura-capellambse.md) -- these tests stub
    fast_reader directly instead of running it against a real model, since
    that side is already covered by tests/test_fast_reader.py against the
    real fixtures."""

    def test_fast_reader_hit_skips_subprocess_entirely(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess should not run when fast_reader succeeds")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        monkeypatch.setattr(
            bridge.fast_reader, "list_layers", lambda abs_path: {"layers": [{"layer": "oa", "present": True}]}
        )
        assert bridge.list_layers("demo.aird") == {"layers": [{"layer": "oa", "present": True}]}

    def test_fast_reader_not_found_short_circuits_without_subprocess(
        self, models_root, workspace_root, monkeypatch
    ):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess should not run for a genuine NotFound")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)

        def _raise_not_found(abs_path, element_id):
            raise bridge.fast_reader.NotFound(f"element not found: {element_id}")

        monkeypatch.setattr(bridge.fast_reader, "get_element", _raise_not_found)
        with pytest.raises(bridge.BridgeError, match="element not found"):
            bridge.get_element("demo.aird", "missing-id")

    def test_fast_reader_generic_error_falls_back_to_headless(self, models_root, workspace_root, monkeypatch):
        expected = {"id": "x", "label": "X", "type": "LogicalComponent"}
        monkeypatch.setattr(bridge, "_spawn_and_wait", _run_writing(expected))

        def _raise(abs_path, element_id):
            raise AttributeError("simulated capellambse coverage gap")

        monkeypatch.setattr(bridge.fast_reader, "get_element", _raise)
        assert bridge.get_element("demo.aird", "x") == expected

    def test_list_elements_no_type_filter_falls_back_to_headless(
        self, models_root, workspace_root, monkeypatch
    ):
        """fast_reader.list_elements always raises NotImplementedError when
        type_filter is None (no clean capellambse equivalent, see its own
        comment) -- confirm that reaches headless rather than erroring out."""
        expected = {"elements": []}
        monkeypatch.setattr(bridge, "_spawn_and_wait", _run_writing(expected))
        assert bridge.list_elements("demo.aird", "la") == expected


class TestExportDiagram:
    def test_export_diagram_recursive_glob_finds_nested_files(
        self, models_root, workspace_root, monkeypatch
    ):
        """Regression test: the real Capella exporter writes into a NESTED
        subdirectory (<out_dir>/<project>/<model>.aird/*.png), not directly
        under out_dir -- a non-recursive glob previously always reported
        files: [] even on a fully successful export."""

        def _run(cmd, capture_output, text, timeout):
            out_arg = cmd[cmd.index("-outputfolder") + 1]
            # out_arg is a workspace-relative resource path
            # ("/models/demo_diagram_exports"); map it back to the real
            # filesystem location under models_root the same way
            # _workspace_path_for_model derives it.
            rel = out_arg.split("/", 2)[-1]
            out_dir = models_root / rel
            nested = out_dir / "some_project" / "demo.aird"
            nested.mkdir(parents=True)
            (nested / "Some Diagram.png").write_bytes(b"\x89PNG")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        result = bridge.export_diagram("demo.aird")
        assert len(result["files"]) == 1
        assert result["files"][0].endswith("Some Diagram.png")


class TestCreateDiagramBreakdown:
    """create_diagram chains 3 headless calls; mock subprocess.run to feed
    each pass a plausible result in sequence and check every generated
    script at least compiles."""

    def _mock_sequence(self, monkeypatch, results):
        captured = {"scripts": []}
        results_iter = iter(results)

        def _run(cmd, capture_output, text, timeout):
            call_dir = Path(cmd[cmd.index("-data") + 1])
            script = (call_dir / bridge._SCRIPT_PROJECT_NAME / "script.py").read_text()
            captured["scripts"].append(script)
            compile(script, "<script>", "exec")
            (call_dir / "result.json").write_text(json.dumps(next(results_iter)))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        return captured

    def test_three_pass_round_trip(self, models_root, workspace_root, monkeypatch):
        pass1_result = {
            "type_name": "LogicalComponent",
            "root_id": "root-id",
            "tree": [
                {"id": "root-id", "label": "Root", "parent_id": None, "depth": 0},
                {"id": "child-id", "label": "Child", "parent_id": "root-id", "depth": 1},
            ],
            "relations": [],
            "diagram_name": "Test Diagram",
        }
        pass2_result = {"new_node_ids": ["child-id"]}
        pass3_result = {
            "diagram_uid": "uid-1",
            "diagram_name": "Test Diagram",
            "node_count": 1,
            "edge_count": 0,
        }
        captured = self._mock_sequence(
            monkeypatch, [pass1_result, pass2_result, pass3_result]
        )
        result = bridge.create_diagram("demo.aird", "la", type_name="LogicalComponent")

        assert result == {
            "diagram_uid": "uid-1",
            "diagram_name": "Test Diagram",
            "type_name": "LogicalComponent",
            "root_id": "root-id",
            "node_count": 1,
            "edge_count": 0,
        }
        assert len(captured["scripts"]) == 3
        assert "get_representation_definition_by_name" in captured["scripts"][0]
        assert "apply_mapping" in captured["scripts"][1]
        assert "set_bounds" in captured["scripts"][2]

    def test_unknown_combo_rejected_before_subprocess(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for an unknown combo")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        with pytest.raises(bridge.BridgeError, match="no breakdown diagram known"):
            bridge.create_diagram("demo.aird", "la", type_name="NotARealType")

    def test_region_mode_state_machine_round_trip(self, models_root, workspace_root, monkeypatch):
        """("la", "Region") reuses the same breakdown algorithm as the other
        8 combos but is NodeMapping (MSM_ModeState), not the
        ContainerMapping "Blank" family -- confirmed live to paint
        correctly headless, unlike CONTAINER_DIAGRAMS' entries."""
        pass1_result = {
            "type_name": "Region",
            "root_id": "region-id",
            "tree": [
                {"id": "region-id", "label": "Region1", "parent_id": None, "depth": 0},
                {"id": "state-id", "label": "Idle", "parent_id": "region-id", "depth": 1},
            ],
            "relations": [],
            "diagram_name": "Region Breakdown - Region1",
        }
        pass2_result = {"new_node_ids": ["state-id"]}
        pass3_result = {
            "diagram_uid": "uid-1",
            "diagram_name": "Region Breakdown - Region1",
            "node_count": 1,
            "edge_count": 0,
        }
        captured = self._mock_sequence(monkeypatch, [pass1_result, pass2_result, pass3_result])
        result = bridge.create_diagram("demo.aird", "la", root_id="region-id")

        assert result["type_name"] == "Region"
        assert "MSM_ModeState" in captured["scripts"][1]


class TestCreateContainerDiagram:
    """create_container_diagram chains 2 headless calls (create+populate,
    then reopen for set_bounds) -- see CONTAINER_DIAGRAMS' comment in
    bridge.py for why this is a separate algorithm from create_diagram."""

    def _mock_sequence(self, monkeypatch, results):
        captured = {"scripts": []}
        results_iter = iter(results)

        def _run(cmd, capture_output, text, timeout):
            call_dir = Path(cmd[cmd.index("-data") + 1])
            script = (call_dir / bridge._SCRIPT_PROJECT_NAME / "script.py").read_text()
            captured["scripts"].append(script)
            compile(script, "<script>", "exec")
            (call_dir / "result.json").write_text(json.dumps(next(results_iter)))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        return captured

    def test_two_pass_round_trip(self, models_root, workspace_root, monkeypatch):
        pass1_result = {
            "type_name": "OperationalEntity",
            "tree": [
                {"id": "root-a", "label": "Root A", "parent_id": None, "depth": 0},
                {"id": "root-b", "label": "Root B", "parent_id": None, "depth": 0},
                {"id": "child-a", "label": "Child A", "parent_id": "root-a", "depth": 1},
            ],
            "diagram_name": "Test Blank",
        }
        pass2_result = {
            "diagram_uid": "uid-1",
            "diagram_name": "Test Blank",
            "node_count": 3,
            "edge_count": 0,
        }
        captured = self._mock_sequence(monkeypatch, [pass1_result, pass2_result])
        result = bridge.create_container_diagram("demo.aird", "oa", "OperationalEntity")

        assert result == {
            "diagram_uid": "uid-1",
            "diagram_name": "Test Blank",
            "type_name": "OperationalEntity",
            "node_count": 3,
            "edge_count": 0,
        }
        assert len(captured["scripts"]) == 2
        assert "OAB_Entity1" in captured["scripts"][0]
        # regression guard for the DiagramServices.createContainer() fix
        # (bridge.py's CONTAINER_DIAGRAMS comment) -- must NOT use
        # python4capella's apply_mapping() to create the container itself.
        assert "DiagramServices.getDiagramServices()" in captured["scripts"][0]
        assert "diagram_services.createContainer" in captured["scripts"][0]
        assert "create_representation" in captured["scripts"][0]
        assert "set_bounds" in captured["scripts"][1]
        # regression guard: nested-container elements must be reachable,
        # not just top-level ones (see the _collect() helper in bridge.py).
        assert "getOwnedDiagramElements" in captured["scripts"][1]

    def test_oaib_includes_edge_mapping_and_relations(self, models_root, workspace_root, monkeypatch):
        """("oa", "OperationalActivity") is the one entry with an
        edge_mapping -- generated script must collect functional-exchange
        relations and reference OAIB Interaction, unlike the OAB case
        above which has no edges at all."""
        pass1_result = {
            "type_name": "OperationalActivity",
            "tree": [
                {"id": "root-a", "label": "Root A", "parent_id": None, "depth": 0},
                {"id": "child-a", "label": "Child A", "parent_id": "root-a", "depth": 1},
            ],
            "relations": [{"source_id": "root-a", "target_id": "child-a", "label": "exchange"}],
            "diagram_name": "OAIB Test",
        }
        pass2_result = {
            "diagram_uid": "uid-2",
            "diagram_name": "OAIB Test",
            "node_count": 2,
            "edge_count": 1,
        }
        captured = self._mock_sequence(monkeypatch, [pass1_result, pass2_result])
        result = bridge.create_container_diagram("demo.aird", "oa", "OperationalActivity")

        assert result["edge_count"] == 1
        assert "OAIB Interaction" in captured["scripts"][0]
        assert "get_owned_functional_exchanges" in captured["scripts"][0]
        assert "setSourceNode" in captured["scripts"][0]

    def test_unknown_combo_rejected_before_subprocess(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for an unknown combo")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        with pytest.raises(bridge.BridgeError, match="no container/blank diagram known"):
            bridge.create_container_diagram("demo.aird", "la", "LogicalComponent")


class TestCreateClassDiagram:
    """create_class_diagram (CDB) is heterogeneous -- DataPkg containers
    nest both sub-DataPkgs and Classes, two different ContainerMappings --
    unlike create_container_diagram's single-mapping shape."""

    def _mock_sequence(self, monkeypatch, results):
        captured = {"scripts": []}
        results_iter = iter(results)

        def _run(cmd, capture_output, text, timeout):
            call_dir = Path(cmd[cmd.index("-data") + 1])
            script = (call_dir / bridge._SCRIPT_PROJECT_NAME / "script.py").read_text()
            captured["scripts"].append(script)
            compile(script, "<script>", "exec")
            (call_dir / "result.json").write_text(json.dumps(next(results_iter)))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        return captured

    def test_two_pass_round_trip(self, models_root, workspace_root, monkeypatch):
        pass1_result = {
            "tree": [
                {"id": "pkg-a", "label": "Data", "parent_id": None, "depth": 0, "kind": "DataPkg"},
                {"id": "cls-a", "label": "Speed", "parent_id": "pkg-a", "depth": 1, "kind": "Class"},
            ],
            "diagram_name": "Class Diagram Blank - Data",
        }
        pass2_result = {
            "diagram_uid": "uid-1",
            "diagram_name": "Class Diagram Blank - Data",
            "node_count": 2,
        }
        captured = self._mock_sequence(monkeypatch, [pass1_result, pass2_result])
        result = bridge.create_class_diagram("demo.aird", "oa")

        assert result == {
            "diagram_uid": "uid-1",
            "diagram_name": "Class Diagram Blank - Data",
            "node_count": 2,
        }
        assert len(captured["scripts"]) == 2
        assert "DT_DataPkg" in captured["scripts"][0]
        assert "DT_Class" in captured["scripts"][0]
        assert "get_data_pkg" in captured["scripts"][0]
        # both mappings are ContainerMappings -- same createContainer() fix
        # as create_container_diagram, not python4capella's apply_mapping().
        assert "diagram_services.createContainer" in captured["scripts"][0]
        assert "set_bounds" in captured["scripts"][1]

    def test_unknown_layer_rejected_before_subprocess(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for an unknown layer")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        with pytest.raises(bridge.BridgeError, match="unknown layer"):
            bridge.create_class_diagram("demo.aird", "not-a-layer")


class TestCreateCapabilityDiagram:
    """create_capability_diagram (OCB) creates entities/actors as
    containers (createContainer, same fix as CONTAINER_DIAGRAMS), every
    involved Operational Capability as a FREE node -- deduped, not nested
    in any entity's container, per oa.odesign's real definition (see the
    long comment above CONTAINER_DIAGRAMS-adjacent constants for the shape
    correction and why the original nested version was wrong) -- and an
    involvement edge (createEdge, source=capability, target=entity) for
    each entity a capability is involved with. Its createRepresentation
    target is OperationalCapabilityPkg, not the entity/EntityPkg every
    other diagram function in this module uses (confirmed live: those all
    silently return None here)."""

    def _mock_sequence(self, monkeypatch, results):
        captured = {"scripts": []}
        results_iter = iter(results)

        def _run(cmd, capture_output, text, timeout):
            call_dir = Path(cmd[cmd.index("-data") + 1])
            script = (call_dir / bridge._SCRIPT_PROJECT_NAME / "script.py").read_text()
            captured["scripts"].append(script)
            compile(script, "<script>", "exec")
            (call_dir / "result.json").write_text(json.dumps(next(results_iter)))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        return captured

    def test_two_pass_round_trip(self, models_root, workspace_root, monkeypatch):
        pass1_result = {
            "tree": [
                {"id": "entity-a", "label": "Driver", "parent_id": None, "depth": 0, "kind": "Entity"},
                {"id": "entity-b", "label": "Vehicle", "parent_id": None, "depth": 0, "kind": "Entity"},
                {"id": "cap-a", "label": "Show Speed", "parent_id": None, "depth": 1, "kind": "Capability"},
            ],
            "diagram_name": "Operational Capabilities Blank - Operational Entities",
            "edge_count": 2,
        }
        pass2_result = {
            "diagram_uid": "uid-1",
            "diagram_name": "Operational Capabilities Blank - Operational Entities",
            "node_count": 3,
        }
        captured = self._mock_sequence(monkeypatch, [pass1_result, pass2_result])
        result = bridge.create_capability_diagram("demo.aird")

        assert result == {
            "diagram_uid": "uid-1",
            "diagram_name": "Operational Capabilities Blank - Operational Entities",
            "node_count": 3,
            "edge_count": 2,
        }
        assert len(captured["scripts"]) == 2
        assert "COC_OperationalEntities" in captured["scripts"][0]
        assert "COC_OperationalCapabilities" in captured["scripts"][0]
        assert "COC_EntityOperationalCapabilityInvolvement" in captured["scripts"][0]
        assert "get_involving_operational_capabilities" in captured["scripts"][0]
        assert "get_operational_capability_pkg" in captured["scripts"][0]
        # all three element kinds go through DiagramServices, not
        # python4capella's apply_mapping() (see CONTAINER_DIAGRAMS' comment
        # and this class's docstring for why).
        assert "diagram_services.createContainer" in captured["scripts"][0]
        assert "diagram_services.createNode" in captured["scripts"][0]
        assert "diagram_services.createEdge" in captured["scripts"][0]
        # regression guard: capabilities must be deduped (one createNode
        # call per unique capability, not one per involving entity).
        assert "capabilities_by_id" in captured["scripts"][0]
        assert "set_bounds" in captured["scripts"][1]


class TestCreateScenarioDiagram:
    """create_scenario_diagram (OES/OAS sequence diagrams) is 2-pass like
    create_capability_diagram, but for a different reason: pass 1 creates
    the representation + InstanceRole nodes only; the bordered "default
    execution" child nodes BasicMessageMapping's source/targetMapping
    actually point at (confirmed in oa.odesign) only materialize after a
    save+reopen cycle, so pass 2 (reopen) creates Message edges and runs
    Sirius's own sequence-diagram ordering-repair chain
    (DialectManager.refresh() + RefreshLayoutCommand) once, right before
    the final save -- the actual export-time-NPE fix, see the long comment
    above _SCENARIO_DIAGRAM_MAPPINGS in bridge.py."""

    def _mock_sequence(self, monkeypatch, results):
        captured = {"scripts": []}
        results_iter = iter(results)

        def _run(cmd, capture_output, text, timeout):
            call_dir = Path(cmd[cmd.index("-data") + 1])
            script = (call_dir / bridge._SCRIPT_PROJECT_NAME / "script.py").read_text()
            captured["scripts"].append(script)
            compile(script, "<script>", "exec")
            (call_dir / "result.json").write_text(json.dumps(next(results_iter)))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        return captured

    def test_unknown_scenario_kind_rejected_before_subprocess(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess should not be called for an invalid scenario_kind")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        with pytest.raises(bridge.BridgeError, match="unknown scenario_kind"):
            bridge.create_scenario_diagram("demo.aird", "scenario-id", scenario_kind="XYZ")

    def test_two_pass_round_trip_oes(self, models_root, workspace_root, monkeypatch):
        pass1_result = {
            "diagram_name": "Operational Interaction Scenario - My Scenario",
            "node_count": 2,
        }
        pass2_result = {
            "diagram_uid": "uid-1",
            "diagram_name": "Operational Interaction Scenario - My Scenario",
            "edge_count": 1,
        }
        captured = self._mock_sequence(monkeypatch, [pass1_result, pass2_result])
        result = bridge.create_scenario_diagram("demo.aird", "scenario-id", scenario_kind="OES")

        assert result == {
            "diagram_uid": "uid-1",
            "diagram_name": "Operational Interaction Scenario - My Scenario",
            "node_count": 2,
            "edge_count": 1,
        }
        assert len(captured["scripts"]) == 2
        assert "Instancerole Mapping OA" in captured["scripts"][0]
        assert "diagram_services.createNode" in captured["scripts"][0]
        assert "DialectManager" in captured["scripts"][0]
        # pass 2: message edges + the ordering-repair fix, not pass 1
        assert "Basic message mapping OA" in captured["scripts"][1]
        assert "getOwnedBorderedNodes" in captured["scripts"][1]
        assert "diagram_services.createEdge" in captured["scripts"][1]
        assert "getCoveredInstanceRoles" in captured["scripts"][1]
        assert "RefreshLayoutCommand" in captured["scripts"][1]
        assert "SiriusGMFHelper" in captured["scripts"][1]
        assert "getDeclaredConstructors" in captured["scripts"][1]

    def test_oas_uses_activity_scenario_mappings(self, models_root, workspace_root, monkeypatch):
        pass1_result = {"diagram_name": "Activity Interaction Scenario - My Scenario", "node_count": 2}
        pass2_result = {
            "diagram_uid": "uid-1",
            "diagram_name": "Activity Interaction Scenario - My Scenario",
            "edge_count": 1,
        }
        captured = self._mock_sequence(monkeypatch, [pass1_result, pass2_result])
        bridge.create_scenario_diagram("demo.aird", "scenario-id", scenario_kind="OAS")

        assert "Activity Interaction Scenario" in captured["scripts"][0]
        assert "InstanceRoleMaping AIS" in captured["scripts"][0]
        assert "Basic message mapping AIS" in captured["scripts"][1]


class TestLayoutDiagram:
    """layout_diagram applies Capella's native 'Layout > All' to an existing
    diagram via GMF's OffscreenEditPartFactory + ArrangeRequest (single
    headless pass).  The script is validated for syntax and key Java classes."""

    def _capture_and_compile(self, monkeypatch, result_data=None):
        captured = {}

        def _run(cmd, capture_output, text, timeout):
            call_dir = Path(cmd[cmd.index("-data") + 1])
            script = (call_dir / bridge._SCRIPT_PROJECT_NAME / "script.py").read_text()
            captured["script"] = script
            compile(script, "<script>", "exec")
            (call_dir / "result.json").write_text(json.dumps(result_data or {"ok": True}))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _run)
        return captured

    def test_success(self, models_root, workspace_root, monkeypatch):
        result = {
            "success": True,
            "diagram_uid": "uid-1",
            "diagram_name": "Operational Entity Breakdown",
        }
        captured = self._capture_and_compile(monkeypatch, result)
        bridge.layout_diagram("demo.aird", "uid-1")

        script = captured["script"]
        assert "OffscreenEditPartFactory" in script
        assert "ArrangeRequest" in script
        assert "ACTION_ARRANGE_ALL" in script
        assert "SiriusGMFHelper" in script
        assert "getGmfDiagram" in script
        assert "createDiagramEditPart" in script
        assert "canExecute" in script
        assert "model.save()" in script
        # error handling code is always present in the template; verify
        # the success-path key classes are there (already checked above).

    def test_uses_sirius_offscreen_edit_part_factory(self, models_root, workspace_root, monkeypatch):
        """Regression guard: must use Sirius's OWN OffscreenEditPartFactory
        subclass (org.eclipse.sirius.diagram.ui.tools.internal.part), not
        GMF's base one -- Sirius's own class javadoc states this exists
        because of "a problem in the default DiagramGraphicalViewer" for
        Sirius diagrams. Since that subclass drops the base class's own
        deferred-update flush loop, the script must replicate it."""
        captured = self._capture_and_compile(monkeypatch)
        bridge.layout_diagram("demo.aird", "uid-1")
        script = captured["script"]
        assert "org.eclipse.sirius.diagram.ui.tools.internal.part.OffscreenEditPartFactory" in script
        assert "org.eclipse.sirius.diagram.ui" in script
        assert "readAndDispatch" in script

    def test_arrange_request_uses_real_action_ids_value(self, models_root, workspace_root, monkeypatch):
        """Regression guard for the exact bug found and fixed 2026-08-18:
        the request type must be the real runtime VALUE of ActionIds.
        ACTION_ARRANGE_ALL ("arrangeAllAction"), read reflectively, not the
        Java source identifier name ("ACTION_ARRANGE_ALL") passed directly
        as the request-type string -- every downstream type-dispatch check
        does a plain string .equals(), so the wrong literal silently
        matched nothing and getCommand() always returned null (no
        exception, for every diagram type, regardless of .activate())."""
        captured = self._capture_and_compile(monkeypatch)
        bridge.layout_diagram("demo.aird", "uid-1")
        script = captured["script"]
        assert "ActionIds" in script
        assert 'getField("ACTION_ARRANGE_ALL")' in script
        assert '_object_array(["ACTION_ARRANGE_ALL"])' not in script

    def test_diagram_not_found(self, models_root, workspace_root, monkeypatch):
        result = {"error": "diagram not found: nonexistent-uid"}
        captured = self._capture_and_compile(monkeypatch, result)
        with pytest.raises(bridge.BridgeError, match="diagram not found"):
            bridge.layout_diagram("demo.aird", "nonexistent-uid")

    def test_layout_command_not_executable(self, models_root, workspace_root, monkeypatch):
        result = {
            "error": "layout command could not be executed for this diagram",
            "diagram_uid": "uid-1",
            "diagram_name": "Some Diagram",
        }
        captured = self._capture_and_compile(monkeypatch, result)
        with pytest.raises(bridge.BridgeError, match="layout command could not be executed"):
            bridge.layout_diagram("demo.aird", "uid-1")

    def test_script_uses_diagram_include(self, models_root, workspace_root, monkeypatch):
        """layout_diagram must include diagram.py for get_all_diagrams()."""
        captured = self._capture_and_compile(monkeypatch)
        bridge.layout_diagram("demo.aird", "uid-1")
        assert "simplified_api/diagram.py" in captured["script"]

    def test_script_resolves_model_path(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch)
        bridge.layout_diagram("demo.aird", "uid-1")
        assert "demo.aird" in captured["script"]

    def test_script_passes_diagram_uid(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch)
        bridge.layout_diagram("demo.aird", "my-uid-123")
        assert "my-uid-123" in captured["script"]

    def test_shell_reflection_pattern(self, models_root, workspace_root, monkeypatch):
        """Verify the Shell constructor is found via reflection (same pattern
        as RefreshLayoutCommand in create_scenario_diagram)."""
        captured = self._capture_and_compile(monkeypatch)
        bridge.layout_diagram("demo.aird", "uid-1")
        script = captured["script"]
        assert "getDeclaredConstructors" in script
        assert "ShellCls" in script
        assert "DisplayCls" in script


class TestAttachMode:
    """No real Capella/Capella GUI involved -- attach_listener.py's half of
    the protocol is stood in for by directly writing/reading the same
    files it would, from the test itself (see the _fake_listener_writes_
    helper below). See docs/decisions/0006-attach-mode-gui-aberta.md."""

    def _fresh_heartbeat(self, attach_root: Path, open_models: list[str], pid: int = 4321, age: float = 0.0):
        workspace_dir = attach_root / "abc123"
        workspace_dir.mkdir(parents=True)
        heartbeat = {
            "pid": pid,
            "workspace_path": "/fake/gui/workspace",
            "open_models": open_models,
            "heartbeat_ts": time.time() - age,
        }
        (workspace_dir / "heartbeat.json").write_text(json.dumps(heartbeat))
        return workspace_dir

    # -- _attach_target ---------------------------------------------------

    def test_attach_target_none_when_root_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bridge, "ATTACH_ROOT", tmp_path / "does-not-exist")
        assert bridge._attach_target(Path("/models/demo.aird")) is None

    def test_attach_target_none_when_heartbeat_stale(self, tmp_path, monkeypatch):
        attach_root = tmp_path / "attach"
        monkeypatch.setattr(bridge, "ATTACH_ROOT", attach_root)
        target = Path("/models/demo.aird")
        self._fresh_heartbeat(attach_root, [str(target)], age=999)
        assert bridge._attach_target(target) is None

    def test_attach_target_none_when_model_not_open(self, tmp_path, monkeypatch):
        attach_root = tmp_path / "attach"
        monkeypatch.setattr(bridge, "ATTACH_ROOT", attach_root)
        self._fresh_heartbeat(attach_root, ["/models/other.aird"])
        assert bridge._attach_target(Path("/models/demo.aird")) is None

    def test_attach_target_found(self, tmp_path, monkeypatch):
        attach_root = tmp_path / "attach"
        monkeypatch.setattr(bridge, "ATTACH_ROOT", attach_root)
        target = Path("/models/demo.aird")
        self._fresh_heartbeat(attach_root, [str(target)], pid=999)
        info = bridge._attach_target(target)
        assert info is not None
        assert info.pid == 999
        assert info.requests_dir.name == "requests"

    # -- _run_script_attach -------------------------------------------------

    def _fake_listener_writes(self, requests_dir: Path, payload: dict):
        """Stand-in for attach_listener.py's request loop: picks up the one
        pending *.request.json and writes its *.result.json, exactly like
        the real listener would (minus actually touching Capella)."""

        def _respond(*_args, **_kwargs):
            req_files = list(requests_dir.glob("*.request.json"))
            assert len(req_files) == 1, "expected exactly one pending request"
            req_id = req_files[0].name.removesuffix(".request.json")
            (requests_dir / f"{req_id}.result.json").write_text(json.dumps(payload))

        return _respond

    def test_run_script_attach_happy_path(self, tmp_path, monkeypatch):
        requests_dir = tmp_path / "requests"
        attach = bridge.AttachInfo(heartbeat_path=tmp_path / "heartbeat.json", requests_dir=requests_dir, pid=111)
        monkeypatch.setattr(bridge.time, "sleep", self._fake_listener_writes(requests_dir, {"ok": True}))

        result = bridge._run_script_attach("body-text", attach, Path("/models/demo.aird"), timeout=5)

        assert result == {"ok": True}
        assert list(requests_dir.glob("*.json")) == []  # request + result both cleaned up

    def test_run_script_attach_request_carries_model_path(self, tmp_path, monkeypatch):
        # attach_listener.py needs this to bind CapellaModel to the right
        # already-open Session -- the body text alone only has a
        # spawn-mode-style workspace-relative path, meaningless in a real
        # GUI workspace (see attach_listener.py's _bound_capella_model).
        requests_dir = tmp_path / "requests"
        attach = bridge.AttachInfo(heartbeat_path=tmp_path / "heartbeat.json", requests_dir=requests_dir, pid=111)
        captured = {}

        def _respond(*_args, **_kwargs):
            req_files = list(requests_dir.glob("*.request.json"))
            payload = json.loads(req_files[0].read_text())
            captured["model_path"] = payload.get("model_path")
            req_id = req_files[0].name.removesuffix(".request.json")
            (requests_dir / f"{req_id}.result.json").write_text(json.dumps({"ok": True}))

        monkeypatch.setattr(bridge.time, "sleep", _respond)
        bridge._run_script_attach("body-text", attach, Path("/home/x/demo.aird"), timeout=5)

        assert captured["model_path"] == "/home/x/demo.aird"

    def test_run_script_attach_error_result_raises_bridge_error(self, tmp_path, monkeypatch):
        requests_dir = tmp_path / "requests"
        attach = bridge.AttachInfo(heartbeat_path=tmp_path / "heartbeat.json", requests_dir=requests_dir, pid=222)
        monkeypatch.setattr(
            bridge.time, "sleep", self._fake_listener_writes(requests_dir, {"error": "boom", "traceback": "tb"})
        )

        with pytest.raises(bridge.BridgeError, match="boom"):
            bridge._run_script_attach("body-text", attach, Path("/models/demo.aird"), timeout=5)

    def test_run_script_attach_timeout_raises_attach_unavailable(self, tmp_path):
        requests_dir = tmp_path / "requests"
        attach = bridge.AttachInfo(heartbeat_path=tmp_path / "heartbeat.json", requests_dir=requests_dir, pid=333)

        with pytest.raises(bridge._AttachUnavailable):
            bridge._run_script_attach("body-text", attach, Path("/models/demo.aird"), timeout=0)

        # a listener that never answers must not leave the request behind
        assert list(requests_dir.glob("*.request.json")) == []

    # -- _dispatch ----------------------------------------------------------

    def test_dispatch_uses_attach_and_never_spawns(self, tmp_path, monkeypatch):
        attach_root = tmp_path / "attach"
        monkeypatch.setattr(bridge, "ATTACH_ROOT", attach_root)
        target = tmp_path / "models" / "demo.aird"
        workspace_dir = self._fresh_heartbeat(attach_root, [str(target)])
        requests_dir = workspace_dir / "requests"

        def _spawn_should_not_run(*_a, **_kw):
            raise AssertionError("spawn mode must not run when attach is available")

        monkeypatch.setattr(bridge, "_run_script", _spawn_should_not_run)
        monkeypatch.setattr(bridge.time, "sleep", self._fake_listener_writes(requests_dir, {"attached": True}))

        assert bridge._dispatch(target, "body-text") == {"attached": True}

    def test_dispatch_falls_back_to_spawn_when_attach_unavailable(self, tmp_path, monkeypatch):
        attach_root = tmp_path / "attach"
        monkeypatch.setattr(bridge, "ATTACH_ROOT", attach_root)
        target = tmp_path / "models" / "demo.aird"
        self._fresh_heartbeat(attach_root, [str(target)])

        def _raise_unavailable(_body, _attach, _model_path):
            raise bridge._AttachUnavailable("listener not responding")

        monkeypatch.setattr(bridge, "_run_script_attach", _raise_unavailable)
        monkeypatch.setattr(bridge, "_run_script", lambda body, timeout=None: {"spawned": True})

        assert bridge._dispatch(target, "body-text") == {"spawned": True}

    def test_dispatch_ignored_when_no_attach_target(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bridge, "ATTACH_ROOT", tmp_path / "no-such-dir")

        monkeypatch.setattr(bridge, "_run_script", lambda body, timeout=None: {"spawned": True})

        assert bridge._dispatch(tmp_path / "models" / "demo.aird", "body-text") == {"spawned": True}

    # -- end-to-end through a real public dispatcher -------------------------

    def test_get_element_uses_attach_when_gui_has_it_open(self, models_root, tmp_path, monkeypatch):
        attach_root = tmp_path / "attach"
        monkeypatch.setattr(bridge, "ATTACH_ROOT", attach_root)
        abs_path = (models_root / "demo.aird").resolve()
        workspace_dir = self._fresh_heartbeat(attach_root, [str(abs_path)])
        requests_dir = workspace_dir / "requests"

        def _spawn_should_not_run(*_a, **_kw):
            raise AssertionError("spawn mode must not run when attach is available")

        monkeypatch.setattr(bridge, "_spawn_and_wait", _spawn_should_not_run)
        payload = {"id": "x1", "label": "Foo", "type": "OperationalActivity"}
        monkeypatch.setattr(bridge.time, "sleep", self._fake_listener_writes(requests_dir, payload))

        assert bridge.get_element("demo.aird", "x1") == payload

    def test_get_element_prefers_attach_over_fast_reader(self, models_root, tmp_path, monkeypatch):
        """Regression: attach must be tried BEFORE fast_reader for reads --
        fast_reader only ever sees the last *saved* disk state, so if it
        ran first (and, as it normally would, succeeded) it would silently
        shadow attach forever and the user's live unsaved GUI edits would
        never be visible through the MCP. This is exactly the ordering
        that broke when fast_reader.py's dispatcher functions and attach
        mode's _dispatch() were first merged together."""
        attach_root = tmp_path / "attach"
        monkeypatch.setattr(bridge, "ATTACH_ROOT", attach_root)
        abs_path = (models_root / "demo.aird").resolve()
        workspace_dir = self._fresh_heartbeat(attach_root, [str(abs_path)])
        requests_dir = workspace_dir / "requests"

        def _fast_reader_should_not_run(*_a, **_kw):
            raise AssertionError("fast_reader must not run when attach is available")

        monkeypatch.setattr(bridge.fast_reader, "get_element", _fast_reader_should_not_run)
        payload = {"id": "x1", "label": "Foo", "type": "OperationalActivity"}
        monkeypatch.setattr(bridge.time, "sleep", self._fake_listener_writes(requests_dir, payload))

        assert bridge.get_element("demo.aird", "x1") == payload
