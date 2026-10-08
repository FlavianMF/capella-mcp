"""Unit tests for the capellambse fast-path reader -- pure Python, no
Capella/Docker/Xvfb required (that's the whole point of this module, see
docs/decisions/0005-camada-leitura-capellambse.md). Runs against the real
fixture models, unlike test_bridge.py's mocked-subprocess tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from capella_mcp import fast_reader

FIXTURE = Path(__file__).parent / "fixtures" / "demo.aird"


class TestListLayers:
    def test_all_layers_present(self):
        result = fast_reader.list_layers(FIXTURE)
        assert result == {
            "layers": [
                {"layer": "oa", "present": True},
                {"layer": "sa", "present": True},
                {"layer": "la", "present": True},
                {"layer": "pa", "present": True},
                {"layer": "epbs", "present": True},
            ]
        }


class TestListElements:
    def test_type_filter_returns_matching_elements(self):
        result = fast_reader.list_elements(FIXTURE, "la", "LogicalComponent")
        assert result["elements"]
        el = result["elements"][0]
        assert el["type"] == "LogicalComponent"
        assert el["label"]
        assert el["id"]

    def test_no_type_filter_has_no_fast_path(self):
        """See fast_reader.list_elements' own comment: the shallow
        eContents() shape headless returns for a bare list_elements call
        has no clean capellambse equivalent -- must raise so bridge.py's
        dispatcher falls back to headless instead of silently returning
        something structurally different."""
        with pytest.raises(NotImplementedError):
            fast_reader.list_elements(FIXTURE, "la", None)

    def test_unknown_layer_raises_not_found(self):
        with pytest.raises(fast_reader.NotFound):
            fast_reader.list_elements(FIXTURE, "not-a-layer", "LogicalComponent")

    def test_unknown_type_filter_propagates_for_fallback(self):
        """Not a NotFound -- an unmapped type is a coverage gap, not a
        "genuinely doesn't exist" result, so the dispatcher must fall back
        to headless rather than treat this as final."""
        with pytest.raises(Exception) as excinfo:
            fast_reader.list_elements(FIXTURE, "la", "TotallyUnknownType")
        assert not isinstance(excinfo.value, fast_reader.NotFound)


class TestGetElement:
    def test_known_element_round_trips(self):
        listed = fast_reader.list_elements(FIXTURE, "la", "LogicalComponent")
        el_id = listed["elements"][0]["id"]
        result = fast_reader.get_element(FIXTURE, el_id)
        assert result["id"] == el_id
        assert result["type"] == "LogicalComponent"

    def test_unknown_id_raises_not_found(self):
        with pytest.raises(fast_reader.NotFound):
            fast_reader.get_element(FIXTURE, "00000000-0000-0000-0000-000000000000")


CAR_HMI = Path(__file__).parent / "fixtures" / "car_hmi" / "car_hmi.aird"
MOTORISTA = "ecf92423-5bda-4eac-b9a8-ecc915d293f0"  # actor (is_actor=True)


class TestOperationalActorVsEntity:
    """capellambse has a single oa.Entity class (is_actor flag) where
    python4capella -- the headless path -- has two wrapper classes,
    OperationalActor and OperationalEntity, over that same EMF class. The
    fast path must answer the same type_filter names with the same split,
    and report the same type names, as headless does."""

    def test_operational_actor_filter_returns_only_actors(self):
        result = fast_reader.list_elements(CAR_HMI, "oa", "OperationalActor")
        assert [(e["label"], e["type"]) for e in result["elements"]] == [
            ("Motorista", "OperationalActor")
        ]

    def test_operational_entity_filter_excludes_actors(self):
        result = fast_reader.list_elements(CAR_HMI, "oa", "OperationalEntity")
        labels = sorted(e["label"] for e in result["elements"])
        assert labels == ["Painel de Instrumentos", "Veículo"]
        assert {e["type"] for e in result["elements"]} == {"OperationalEntity"}

    def test_get_element_reports_actor_type(self):
        assert fast_reader.get_element(CAR_HMI, MOTORISTA)["type"] == "OperationalActor"
