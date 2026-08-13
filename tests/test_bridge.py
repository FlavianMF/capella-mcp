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
