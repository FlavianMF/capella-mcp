"""Unit tests for scripts/register_attach_listener.py -- pure file I/O,
no Capella involved. The exact on-disk format this asserts against was
verified live: a throwaway headless script called the real
org.eclipse.ease.ui.scripts.preferences.PreferencesHelper.addLocation()
API and the resulting .prefs file was read back byte-for-byte -- see
docs/decisions/0006-attach-mode-gui-aberta.md. What this script cannot
verify (only a real Capella GUI boot can) is whether Eclipse's EASE
plugin actually treats a location added this way, before Eclipse has
ever opened that workspace, the same as one added through the
Preferences UI -- see the module docstring's caveat.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "register_attach_listener.py"
_spec = importlib.util.spec_from_file_location("register_attach_listener", _SCRIPT_PATH)
register_attach_listener = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = register_attach_listener
_spec.loader.exec_module(register_attach_listener)


class TestNodeNameEncoding:
    def test_matches_live_verified_encoding(self):
        # Exact input/output pair captured from a real Eclipse-generated
        # .prefs file (see docs/decisions/0006).
        uri = "file:/home/flv/projetos_ita/capella_mcp/.claude/worktrees/attach-mode/src/capella_mcp"
        expected = (
            "file\\:|home|flv|projetos_ita|capella_mcp|.claude|worktrees"
            "|attach-mode|src|capella_mcp"
        )
        assert register_attach_listener._node_name(uri) == expected


class TestRegister:
    def test_writes_three_expected_lines(self, tmp_path):
        listener_dir = tmp_path / "listener"
        listener_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        prefs_path = register_attach_listener.register(workspace_dir, listener_dir=listener_dir)

        content = prefs_path.read_text(encoding="utf-8")
        node = register_attach_listener._node_name(f"file:{listener_dir.as_posix()}")
        assert content.startswith("eclipse.preferences.version=1\n")
        assert f"{node}/default=false" in content
        assert f"{node}/location=file\\:{listener_dir.as_posix()}" in content
        assert f"{node}/recursive=false" in content

    def test_idempotent(self, tmp_path):
        listener_dir = tmp_path / "listener"
        listener_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        register_attach_listener.register(workspace_dir, listener_dir=listener_dir)
        prefs_path = register_attach_listener.register(workspace_dir, listener_dir=listener_dir)

        assert prefs_path.read_text(encoding="utf-8").count("/default=false") == 1

    def test_preserves_other_existing_locations(self, tmp_path):
        listener_dir = tmp_path / "listener"
        listener_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        settings_dir = workspace_dir / ".metadata" / ".plugins" / "org.eclipse.core.runtime" / ".settings"
        settings_dir.mkdir(parents=True)
        existing = settings_dir / "org.eclipse.ease.ui.scripts.prefs"
        existing.write_text(
            "eclipse.preferences.version=1\n"
            "file\\:|some|other|location/default=false\n"
            "file\\:|some|other|location/location=file\\:/some/other/location\n"
            "file\\:|some|other|location/recursive=true\n",
            encoding="utf-8",
        )

        prefs_path = register_attach_listener.register(workspace_dir, listener_dir=listener_dir)

        content = prefs_path.read_text(encoding="utf-8")
        assert "file\\:|some|other|location/recursive=true" in content

    def test_rejects_nonexistent_workspace_dir(self, tmp_path, capsys):
        missing = tmp_path / "does-not-exist"
        rc = register_attach_listener.main(["register_attach_listener.py", str(missing)])
        assert rc != 0
        assert "not a directory" in capsys.readouterr().err
