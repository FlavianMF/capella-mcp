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
