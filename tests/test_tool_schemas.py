"""schemas/model-tools.json must match the tools the server registers.
No Capella/Docker involved."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "dump_tool_schemas", ROOT / "scripts" / "dump_tool_schemas.py"
)
dump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dump)

REGEN = "Regenerate with: uv run python scripts/dump_tool_schemas.py (and commit schemas/model-tools.json)"


def test_committed_schema_is_up_to_date():
    expected = dump.render(dump.build_document())
    actual = dump.SCHEMA_PATH.read_text(encoding="utf-8")
    assert actual == expected, f"schemas/model-tools.json is stale. {REGEN}"


def test_tool_names_unique_and_sorted():
    names = [t["name"] for t in json.loads(dump.SCHEMA_PATH.read_text(encoding="utf-8"))["tools"]]
    assert names, "no tools in schema file"
    assert len(names) == len(set(names))
    assert names == sorted(names)


def test_every_tool_takes_model_path():
    # Every current tool operates on a model file; the plugin relies on it.
    for tool in dump.build_document()["tools"]:
        schema = tool["inputSchema"]
        assert "model_path" in schema["properties"], tool["name"]
        assert "model_path" in schema["required"], tool["name"]
        assert tool["description"], tool["name"]
