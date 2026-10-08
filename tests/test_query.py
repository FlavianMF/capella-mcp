"""Unit tests for the PRD-09 model query tools (capella_mcp.query).

Pure Python against capellambse: the car_hmi fixture, enriched in memory by
conftest.rich_model with allocations, exchanges and involvements. No
Capella/Docker needed.
"""

from __future__ import annotations

import json
import random

import pytest

from capella_mcp import fast_reader, query

ITEM_KEYS = {"id", "label", "type", "layer", "relation", "direction", "depth", "viaId"}


def _by_id(result):
    return {i["id"]: i for i in result["items"]}


# --------------------------------------------------------------------------
# limits
# --------------------------------------------------------------------------


class TestParseLimits:
    def test_defaults(self):
        limits = query.parse_limits("abc")
        assert (limits.element_id, limits.max_results, limits.max_depth) == ("abc", 50, 2)

    @pytest.mark.parametrize(
        "given,expected", [(0, 1), (-5, 1), (1, 1), (200, 200), (201, 200), (10_000, 200)]
    )
    def test_max_results_clamped(self, given, expected):
        assert query.parse_limits("abc", max_results=given).max_results == expected

    @pytest.mark.parametrize("given,expected", [(0, 1), (1, 1), (4, 4), (5, 4), (99, 4)])
    def test_max_depth_clamped(self, given, expected):
        assert query.parse_limits("abc", max_depth=given).max_depth == expected

    @pytest.mark.parametrize("element_id", [None, "", "   "])
    def test_missing_or_blank_id_is_a_clear_error(self, element_id):
        with pytest.raises(query.QueryError, match="element_id is required"):
            query.parse_limits(element_id)

    @pytest.mark.parametrize("bad", ["10", 1.5, True, [1]])
    def test_wrong_type_is_a_clear_error(self, bad):
        with pytest.raises(query.QueryError, match="max_results must be an integer"):
            query.parse_limits("abc", max_results=bad)
        with pytest.raises(query.QueryError, match="max_depth must be an integer"):
            query.parse_limits("abc", max_depth=bad)

    def test_non_string_id_is_a_clear_error(self):
        with pytest.raises(query.QueryError, match="element_id must be a string"):
            query.parse_limits(42)


# --------------------------------------------------------------------------
# result building (FR-3 equivalent)
# --------------------------------------------------------------------------


def _item(id, depth=1, layer="la", type="T", label="x", relation="r", direction="out"):
    return {
        "id": id,
        "label": label,
        "type": type,
        "layer": layer,
        "relation": relation,
        "direction": direction,
        "depth": depth,
        "viaId": "root",
    }


ROOT = {"id": "root", "label": "Root", "type": "T", "layer": "la"}


class TestBuildResult:
    def test_shape(self):
        result = query.build_result(ROOT, [_item("a")], query.parse_limits("root"))
        assert set(result) == {"root", "items", "total", "truncated"}
        assert result["root"] == ROOT
        assert result["total"] == 1
        assert result["truncated"] is False

    def test_sort_order_depth_layer_type_label_id(self):
        items = [
            _item("e", depth=2, layer="oa"),
            _item("d", layer=""),
            _item("c", layer="epbs"),
            _item("b2", layer="sa", type="B", label="y"),
            _item("b1", layer="sa", type="B", label="x"),
            _item("a", layer="sa", type="A", label="z"),
            _item("o", layer="oa"),
        ]
        result = query.build_result(ROOT, items, query.parse_limits("root"))
        assert [i["id"] for i in result["items"]] == ["o", "a", "b1", "b2", "c", "d", "e"]

    def test_dedup_on_id_relation_direction_keeps_shallowest(self):
        items = [_item("a", depth=2), _item("a", depth=1), _item("a", direction="in"), _item("a", relation="q")]
        result = query.build_result(ROOT, items, query.parse_limits("root"))
        assert result["total"] == 3
        assert [(i["relation"], i["direction"], i["depth"]) for i in result["items"]] == [
            ("q", "out", 1),
            ("r", "in", 1),
            ("r", "out", 1),
        ]

    def test_cut_sets_total_and_truncated(self):
        items = [_item(f"id{n:02d}") for n in range(10)]
        result = query.build_result(ROOT, items, query.parse_limits("root", max_results=3))
        assert [i["id"] for i in result["items"]] == ["id00", "id01", "id02"]
        assert result["total"] == 10
        assert result["truncated"] is True

    def test_identical_output_for_shuffled_input(self):
        items = [_item(f"id{n}", depth=n % 3 + 1, layer=["oa", "la", ""][n % 3]) for n in range(30)]
        expected = json.dumps(query.build_result(ROOT, items, query.parse_limits("root")))
        for seed in range(5):
            shuffled = items[:]
            random.Random(seed).shuffle(shuffled)
            assert json.dumps(query.build_result(ROOT, shuffled, query.parse_limits("root"))) == expected

    def test_counts_are_computed_before_the_cut(self):
        items = [_item("a", relation="contained"), _item("b", relation="contained"), _item("c", relation="trace")]
        result = query.build_result(ROOT, items, query.parse_limits("root", max_results=1), with_counts=True)
        assert result["counts"] == {"contained": 2, "trace": 1}
        assert len(result["items"]) == 1


# --------------------------------------------------------------------------
# generic walker (FR-4 equivalent)
# --------------------------------------------------------------------------


class _Node:
    def __init__(self, uuid):
        self.uuid = uuid


def _graph(edges):
    nodes = {}

    def node(n):
        return nodes.setdefault(n, _Node(n))

    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(node(b))
        node(a)

    def neighbors(n):
        return [(m, "r", "out") for m in adj.get(n.uuid, [])]

    return node, neighbors


class TestWalk:
    def test_depth_limit(self):
        node, neighbors = _graph([("r", "a"), ("a", "b"), ("b", "c")])
        found = query.walk(node("r"), neighbors, max_depth=2, cap=100)
        assert [(n.uuid, depth, via) for n, _, _, depth, via in found] == [("a", 1, "r"), ("b", 2, "a")]

    def test_cycle_is_safe_and_root_excluded(self):
        node, neighbors = _graph([("r", "a"), ("a", "r"), ("a", "b"), ("b", "a")])
        found = query.walk(node("r"), neighbors, max_depth=4, cap=100)
        assert [n.uuid for n, *_ in found] == ["a", "b"]

    def test_diamond_visits_each_node_once(self):
        node, neighbors = _graph([("r", "a"), ("r", "b"), ("a", "c"), ("b", "c")])
        found = query.walk(node("r"), neighbors, max_depth=3, cap=100)
        assert sorted(n.uuid for n, *_ in found) == ["a", "b", "c"]

    def test_wide_fan_out_stops_at_cap(self):
        node, neighbors = _graph([("r", f"n{i}") for i in range(500)])
        assert len(query.walk(node("r"), neighbors, max_depth=2, cap=10)) == 10


# --------------------------------------------------------------------------
# the four tools against a real (in-memory enriched) model
# --------------------------------------------------------------------------


class TestCommon:
    @pytest.mark.parametrize(
        "call",
        [
            lambda m: query.find_references(m, "nope"),
            lambda m: query.trace_element(m, "nope"),
            lambda m: query.list_exchanges(m, "nope"),
            lambda m: query.impact_analysis(m, "nope"),
        ],
    )
    def test_unknown_id_uses_get_element_message(self, rich_model, call):
        with pytest.raises(fast_reader.NotFound, match="^element not found: nope$"):
            call(rich_model.model)

    def test_root_and_items_shape(self, rich_model):
        result = query.trace_element(rich_model.model, rich_model.capturar.uuid)
        assert result["root"] == {
            "id": rich_model.capturar.uuid,
            "label": "Capturar velocidade do veículo",
            "type": "LogicalFunction",
            "layer": "la",
        }
        assert result["items"]
        assert all(set(i) == ITEM_KEYS for i in result["items"])


class TestTraceElement:
    def test_in_allocation_finds_allocating_component(self, rich_model):
        result = query.trace_element(
            rich_model.model, rich_model.capturar.uuid, relation="allocation", direction="in"
        )
        assert [(i["id"], i["relation"], i["direction"], i["depth"], i["viaId"]) for i in result["items"]] == [
            (rich_model.fonte.uuid, "ComponentFunctionalAllocation", "in", 1, rich_model.capturar.uuid)
        ]

    def test_out_realization_walks_down_the_layers(self, rich_model):
        m = rich_model.model
        root_pf = m.search("PhysicalFunction")[0]
        result = query.trace_element(m, root_pf.uuid, relation="realization", direction="out", max_depth=3)
        assert [(i["label"], i["layer"], i["depth"], i["relation"]) for i in result["items"]] == [
            ("Root Logical Function", "la", 1, "FunctionRealization"),
            ("Root System Function", "sa", 2, "FunctionRealization"),
            ("Root Operational Activity", "oa", 3, "FunctionRealization"),
        ]
        assert result["items"][1]["viaId"] == rich_model.root_lf.uuid

    def test_max_depth_stops_the_walk(self, rich_model):
        m = rich_model.model
        root_pf = m.search("PhysicalFunction")[0]
        result = query.trace_element(m, root_pf.uuid, relation="realization", direction="out", max_depth=1)
        assert [i["label"] for i in result["items"]] == ["Root Logical Function"]

    def test_both_directions_all_relations(self, rich_model):
        result = query.trace_element(rich_model.model, rich_model.logical_system.uuid, max_depth=1)
        got = {(i["type"], i["direction"], i["relation"]) for i in result["items"]}
        assert got == {
            ("SystemComponent", "out", "ComponentRealization"),
            ("PhysicalComponent", "in", "ComponentRealization"),
        }

    def test_relation_filter_excludes_other_kinds(self, rich_model):
        result = query.trace_element(rich_model.model, rich_model.capturar.uuid, relation="realization")
        assert result["items"] == []

    @pytest.mark.parametrize("kwargs", [{"relation": "involvement"}, {"direction": "sideways"}])
    def test_bad_enum_is_a_clear_error(self, rich_model, kwargs):
        with pytest.raises(query.QueryError):
            query.trace_element(rich_model.model, rich_model.capturar.uuid, **kwargs)


class TestFindReferences:
    def test_lists_non_containment_referrers(self, rich_model):
        result = query.find_references(rich_model.model, rich_model.capturar.uuid)
        got = {(i["type"], i["relation"], i["direction"]) for i in result["items"]}
        assert ("LogicalComponent", "allocated_functions", "in") in got
        assert ("ComponentFunctionalAllocation", "target", "in") in got

    def test_containment_is_excluded(self, rich_model):
        ls = rich_model.logical_system
        result = query.find_references(rich_model.model, ls.uuid)
        assert ls.parent.uuid not in {i["id"] for i in result["items"]}
        assert any(i["relation"] == "type" and i["type"] == "Part" for i in result["items"])

    def test_diagrams_only_when_asked(self, rich_model):
        ihm = rich_model.ihm.uuid
        assert not [i for i in query.find_references(rich_model.model, ihm)["items"] if i["type"] == "Diagram"]
        diagrams = [
            i
            for i in query.find_references(rich_model.model, ihm, include_diagrams=True)["items"]
            if i["type"] == "Diagram"
        ]
        assert [(d["label"], d["relation"], d["layer"]) for d in diagrams] == [
            ("Estrutura IHM de Velocidade", "shown_in", "la")
        ]

    def test_max_results_truncates(self, rich_model):
        result = query.find_references(rich_model.model, rich_model.capturar.uuid, max_results=1)
        assert len(result["items"]) == 1
        assert result["total"] >= 2
        assert result["truncated"] is True


class TestListExchanges:
    def test_component_and_physical_through_ports(self, rich_model):
        result = query.list_exchanges(rich_model.model, rich_model.fonte.uuid)
        assert [(i["id"], i["relation"], i["direction"], i["viaId"]) for i in result["items"]] == sorted(
            [
                (rich_model.ce.uuid, "component", "out", rich_model.ihm.uuid),
                (rich_model.pl.uuid, "physical", "out", rich_model.ihm.uuid),
            ],
            key=lambda t: {"component": "ComponentExchange", "physical": "PhysicalLink"}[t[1]],
        )

    def test_functional_direction_and_other_end(self, rich_model):
        out = query.list_exchanges(rich_model.model, rich_model.capturar.uuid, kind="functional")
        assert [(i["id"], i["direction"], i["viaId"]) for i in out["items"]] == [
            (rich_model.fe.uuid, "out", rich_model.exibir.uuid)
        ]
        inc = query.list_exchanges(rich_model.model, rich_model.exibir.uuid, kind="functional")
        assert [(i["direction"], i["viaId"]) for i in inc["items"]] == [("in", rich_model.capturar.uuid)]

    def test_kind_filter(self, rich_model):
        result = query.list_exchanges(rich_model.model, rich_model.fonte.uuid, kind="physical")
        assert [i["id"] for i in result["items"]] == [rich_model.pl.uuid]
        assert query.list_exchanges(rich_model.model, rich_model.fonte.uuid, kind="functional")["items"] == []

    def test_exchange_directly_between_entities(self, rich_model):
        m = rich_model.model
        cm = m.oa.entity_pkg.exchanges.create(name="olha", source=rich_model.motorista, target=rich_model.veiculo)
        result = query.list_exchanges(m, rich_model.veiculo.uuid, kind="component")
        assert [(i["id"], i["direction"], i["viaId"]) for i in result["items"]] == [
            (cm.uuid, "in", rich_model.motorista.uuid)
        ]

    def test_bad_kind_is_a_clear_error(self, rich_model):
        with pytest.raises(query.QueryError, match="kind"):
            query.list_exchanges(rich_model.model, rich_model.fonte.uuid, kind="data")


class TestImpactAnalysis:
    def test_all_relations_with_counts(self, rich_model):
        result = query.impact_analysis(rich_model.model, rich_model.fonte.uuid)
        by_rel = {}
        for i in result["items"]:
            by_rel.setdefault(i["relation"], set()).add(i["id"])
        assert {"cp_out", "pp1"} <= {
            i["label"] for i in result["items"] if i["relation"] == "contained"
        }
        assert by_rel["trace"] == {rich_model.capturar.uuid}
        assert by_rel["exchange"] == {rich_model.ce.uuid, rich_model.pl.uuid}
        assert [i["label"] for i in result["items"] if i["relation"] == "diagram"] == ["Estrutura IHM de Velocidade"]
        assert "referenced_by" in by_rel
        assert result["counts"] == {rel: len(ids) for rel, ids in by_rel.items()}
        assert result["total"] == len(result["items"])

    def test_contained_walks_to_max_depth(self, rich_model):
        ls = rich_model.logical_system.uuid
        shallow = query.impact_analysis(rich_model.model, ls, max_depth=1)
        deep = query.impact_analysis(rich_model.model, ls, max_depth=2)
        contained = lambda r: {i["label"] for i in r["items"] if i["relation"] == "contained"}  # noqa: E731
        assert {"IHM", "Fonte de Dados do Veículo"} <= contained(shallow)
        assert "cp_out" not in contained(shallow)
        assert "cp_out" in contained(deep)
        cp = next(i for i in deep["items"] if i["label"] == "cp_out")
        assert (cp["depth"], cp["viaId"]) == (2, rich_model.fonte.uuid)

    def test_diagrams_of_contained_elements_count(self, rich_model):
        result = query.impact_analysis(rich_model.model, rich_model.logical_system.uuid)
        assert "Estrutura IHM de Velocidade" in {i["label"] for i in result["items"] if i["relation"] == "diagram"}

    def test_truncated_keeps_full_counts(self, rich_model):
        result = query.impact_analysis(rich_model.model, rich_model.fonte.uuid, max_results=2)
        assert len(result["items"]) == 2
        assert result["truncated"] is True
        assert sum(result["counts"].values()) == result["total"] > 2

    def test_deterministic(self, rich_model):
        a = query.impact_analysis(rich_model.model, rich_model.logical_system.uuid)
        b = query.impact_analysis(rich_model.model, rich_model.logical_system.uuid)
        assert json.dumps(a) == json.dumps(b)
