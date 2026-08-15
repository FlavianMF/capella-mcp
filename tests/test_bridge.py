"""Unit tests for the headless bridge -- subprocess is mocked, no Capella
or Docker required. Integration tests (tests/test_integration.py) cover the
real python4capella API against a built image."""

from __future__ import annotations

import json
import subprocess
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
        monkeypatch.setattr(bridge.subprocess, "run", _run_writing(expected))
        assert bridge.list_layers("demo.aird") == expected

    def test_error_in_result_raises_bridge_error(self, models_root, workspace_root, monkeypatch):
        monkeypatch.setattr(
            bridge.subprocess, "run", _run_writing({"error": "boom", "traceback": "tb"})
        )
        with pytest.raises(bridge.BridgeError, match="boom"):
            bridge.list_layers("demo.aird")

    def test_missing_result_file_raises(self, models_root, workspace_root, monkeypatch):
        def _run(cmd, capture_output, text, timeout):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="something crashed")

        monkeypatch.setattr(bridge.subprocess, "run", _run)
        with pytest.raises(bridge.BridgeError, match="produced no result"):
            bridge.list_layers("demo.aird")

    def test_timeout_raises_bridge_error(self, models_root, workspace_root, monkeypatch):
        def _run(cmd, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr(bridge.subprocess, "run", _run)
        with pytest.raises(bridge.BridgeError, match="timed out"):
            bridge.list_layers("demo.aird")

    def test_unknown_layer_rejected_before_subprocess(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for an invalid layer")

        monkeypatch.setattr(bridge.subprocess, "run", _run)
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

        monkeypatch.setattr(bridge.subprocess, "run", _run)
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

    def test_update_element(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(monkeypatch)
        bridge.update_element("demo.aird", "some-id", {"description": "baz"})
        assert "start_transaction" in captured["script"]

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

    def test_delete_diagram(self, models_root, workspace_root, monkeypatch):
        captured = self._capture_and_compile(
            monkeypatch, {"deleted": True, "uid": "u1", "name": "N"}
        )
        bridge.delete_diagram("demo.aird", "u1")
        assert "DialectManager" in captured["script"]
        assert "deleteRepresentation" in captured["script"]
        assert "start_transaction" in captured["script"]


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

        monkeypatch.setattr(bridge.subprocess, "run", _run)
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

        monkeypatch.setattr(bridge.subprocess, "run", _run)
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

        monkeypatch.setattr(bridge.subprocess, "run", _run)
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

        monkeypatch.setattr(bridge.subprocess, "run", _run)
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
        }
        captured = self._mock_sequence(monkeypatch, [pass1_result, pass2_result])
        result = bridge.create_container_diagram("demo.aird", "oa", "OperationalEntity")

        assert result == {
            "diagram_uid": "uid-1",
            "diagram_name": "Test Blank",
            "type_name": "OperationalEntity",
            "node_count": 3,
        }
        assert len(captured["scripts"]) == 2
        assert "OAB_Entity1" in captured["scripts"][0]
        assert "apply_mapping" in captured["scripts"][0]
        assert "create_representation" in captured["scripts"][0]
        assert "set_bounds" in captured["scripts"][1]
        # regression guard: nested-container elements must be reachable,
        # not just top-level ones (see the _collect() helper in bridge.py).
        assert "getOwnedDiagramElements" in captured["scripts"][1]

    def test_unknown_combo_rejected_before_subprocess(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for an unknown combo")

        monkeypatch.setattr(bridge.subprocess, "run", _run)
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

        monkeypatch.setattr(bridge.subprocess, "run", _run)
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
        assert "set_bounds" in captured["scripts"][1]

    def test_unknown_layer_rejected_before_subprocess(self, models_root, workspace_root, monkeypatch):
        def _run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for an unknown layer")

        monkeypatch.setattr(bridge.subprocess, "run", _run)
        with pytest.raises(bridge.BridgeError, match="unknown layer"):
            bridge.create_class_diagram("demo.aird", "not-a-layer")
