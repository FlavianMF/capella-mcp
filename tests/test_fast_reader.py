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


def _ids(items):
    return [i["id"] for i in items]


class TestGetElementRelations:
    """get_element adds a bounded "relations" map of {id, label, type}
    lists; the original id/label/type keys are unchanged."""

    @pytest.fixture
    def get(self, rich_model, monkeypatch):
        monkeypatch.setattr(fast_reader, "_open", lambda abs_path: rich_model.model)
        return lambda el: fast_reader.get_element(CAR_HMI, el.uuid)

    def test_existing_keys_unchanged(self, get, rich_model):
        result = get(rich_model.ihm)
        assert result["id"] == rich_model.ihm.uuid
        assert result["label"] == "IHM"
        assert result["type"] == "LogicalComponent"

    def test_capability_lists_involved_entities_and_activities(self, get, rich_model):
        rel = get(rich_model.capability)["relations"]
        assert sorted((e["label"], e["type"]) for e in rel["involved_entities"]) == [
            ("Motorista", "OperationalActor"),
            ("Veículo", "OperationalEntity"),
        ]
        assert _ids(rel["involved_activities"]) == [rich_model.monitorar.uuid]

    def test_entity_lists_capabilities_and_allocated_activities(self, get, rich_model):
        rel = get(rich_model.motorista)["relations"]
        assert _ids(rel["involved_capabilities"]) == [rich_model.capability.uuid]
        assert _ids(rel["allocated_activities"]) == [rich_model.monitorar.uuid]

    def test_function_lists_allocation_and_exchanges(self, get, rich_model):
        rel = get(rich_model.capturar)["relations"]
        assert _ids(rel["allocated_to"]) == [rich_model.fonte.uuid]
        assert _ids(rel["exchanges"]) == [rich_model.fe.uuid]

    def test_component_lists_allocated_functions_and_realizations(self, get, rich_model):
        assert _ids(get(rich_model.fonte)["relations"]["allocated_functions"]) == [rich_model.capturar.uuid]
        rel = get(rich_model.logical_system)["relations"]
        assert [i["type"] for i in rel["realized_components"]] == ["SystemComponent"]
        assert [i["type"] for i in rel["realizing_components"]] == ["PhysicalComponent"]

    def test_empty_relations_are_omitted(self, get, rich_model):
        rel = get(rich_model.ihm)["relations"]
        assert all(rel.values())

    def test_relation_lists_are_capped(self, get, rich_model, monkeypatch):
        monkeypatch.setattr(fast_reader, "RELATION_CAP", 1)
        model = rich_model.model
        model.la.root_component.allocated_functions.append(rich_model.capturar)
        model.la.root_component.allocated_functions.append(rich_model.exibir)
        result = get(model.la.root_component)
        assert len(result["relations"]["allocated_functions"]) == 1
        assert result["relations_truncated"] == {"allocated_functions": 2}
