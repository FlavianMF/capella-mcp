"""model_files: id diffing and dangling-reference scan on saved XMI, no Capella."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import model_files as mf
import pytest
from _xmi_edit import drop_aird_node as _drop_aird_node
from _xmi_edit import drop_capella_element as _drop_capella_element

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "car_hmi"
MONITORAR = "b2fd6238-ce55-4bdd-af1a-3779d1570bd2"
FORNECER = "d8ec91aa-619b-46e1-897c-fa62a90347c9"
EXIBIR = "870edb15-7810-4f4c-a240-256b7e95b959"
OABD_NAME = "Atividades de Velocidade do Veículo"


@pytest.fixture
def car_hmi(tmp_path) -> Path:
    dest = tmp_path / "car_hmi"
    shutil.copytree(FIXTURE, dest)
    return dest


def test_fixture_has_no_dangling_refs_and_indexes_the_oabd():
    scan = mf.scan_model(FIXTURE)
    assert scan.files == ["car_hmi.afm", "car_hmi.aird", "car_hmi.capella"]
    assert mf.dangling_refs(scan) == []
    oabd = [d for d in scan.diagrams.values() if d.name == OABD_NAME]
    assert len(oabd) == 1
    assert oabd[0].target_id == EXIBIR
    assert oabd[0].viewed_ids == {MONITORAR, FORNECER}
    assert oabd[0].descriptor_uid == "_0m1rwJdFEfG1msMcddcZ6w"


def test_semantic_defs_are_capella_ids_only():
    scan = mf.scan_model(FIXTURE)
    sem = scan.semantic_defs()
    assert MONITORAR in sem
    assert sem[MONITORAR].xsi_type == "org.polarsys.capella.core.data.oa:OperationalActivity"
    assert sem[MONITORAR].name == "Monitorar velocidade do veículo"
    assert all(not i.startswith("_") for i in sem)


def test_capella_hash_refs_are_parsed():
    scan = mf.scan_model(FIXTURE)
    # .capella line 84: targetElement="#2898e9d8-..." (a realization trace)
    hits = [r for r in scan.refs if r.file == "car_hmi.capella" and r.attr == "targetElement"]
    assert hits and all(r.target_file == "car_hmi.capella" for r in hits)


def test_views_of_reports_shown_and_rooted():
    scan = mf.scan_model(FIXTURE)
    assert mf.views_of(scan, MONITORAR) == [
        {"uid": "_0m1rwJdFEfG1msMcddcZ6w", "name": OABD_NAME, "rooted": False, "shown": True}
    ]
    rooted = mf.views_of(scan, EXIBIR)
    assert rooted == [{"uid": "_0m1rwJdFEfG1msMcddcZ6w", "name": OABD_NAME, "rooted": True, "shown": False}]


def test_raw_delete_is_flagged_as_new_dangling_refs(car_hmi):
    before = mf.scan_model(car_hmi)
    _drop_capella_element(car_hmi, MONITORAR)
    after = mf.scan_model(car_hmi)
    diff = mf.compare_scans(before, after, MONITORAR)

    assert [d["id"] for d in diff["removed_semantic"]] == [MONITORAR]
    assert diff["removed_representation_count"] == 0
    assert diff["target_defined_after"] is False
    dangling = diff["new_dangling"]
    assert {(r["file"], r["tag"], r["target_id"]) for r in dangling} == {
        ("car_hmi.aird", "target", MONITORAR),
        ("car_hmi.aird", "semanticElements", MONITORAR),
    }
    assert len(diff["target_referenced_after"]) == 2
    assert diff["target_shown_in_diagrams_after"] == [OABD_NAME]
    assert diff["diagrams_deleted"] == [] and diff["diagrams_changed"] == []
    assert mf.find_occurrences(car_hmi, MONITORAR)


def test_clean_delete_shows_no_dangling_and_a_changed_diagram(car_hmi):
    before = mf.scan_model(car_hmi)
    _drop_capella_element(car_hmi, MONITORAR)
    _drop_aird_node(car_hmi, MONITORAR)
    after = mf.scan_model(car_hmi)
    diff = mf.compare_scans(before, after, MONITORAR)

    assert diff["new_dangling"] == []
    assert diff["target_referenced_after"] == []
    assert diff["target_shown_in_diagrams_after"] == []
    assert diff["removed_representation_count"] > 0
    assert diff["diagrams_changed"] == [
        {"uid": "_0m1rwJdFEfG1msMcddcZ6w", "name": OABD_NAME, "lost_viewed_ids": [MONITORAR]}
    ]
    assert mf.find_occurrences(car_hmi, MONITORAR) == []


# ---- synthetic models: edge cases the fixture doesn't have -----------------

UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_B = "aaaaaaaa-0000-0000-0000-000000000002"
UUID_C = "aaaaaaaa-0000-0000-0000-000000000003"
UUID_GONE = "aaaaaaaa-0000-0000-0000-00000000dead"
REP = "_RRRRRRRRRRRRRRRRRRRRRR"
DESC = "_DDDDDDDDDDDDDDDDDDDDDD"
NODE = "_NNNNNNNNNNNNNNNNNNNNNN"


def _write(dir_: Path, capella: str, aird: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "m.capella").write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + capella, encoding="utf-8")
    (dir_ / "m.aird").write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + aird, encoding="utf-8")
    return dir_


CAPELLA = f"""<root xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <owned xsi:type="x:Entity" id="{UUID_A}" name="A #{UUID_GONE}"/>
  <owned xsi:type="x:Capability" id="{UUID_B}" name="B">
    <inv xsi:type="x:Involvement" id="{UUID_C}" involved="#{UUID_A}"/>
  </owned>
  <owned xsi:type="x:Trace" id="aaaaaaaa-0000-0000-0000-0000000000ff" multi="#{UUID_A} #{UUID_B}"
      lib="platform:/plugin/foo/bar.ecore#{UUID_GONE}"/>
</root>
"""


def _aird(with_diagram: bool = True, node_target: str = UUID_A) -> str:
    diagram = f"""
  <diagram:DSemanticDiagram uid="{REP}">
    <ownedDiagramElements uid="{NODE}">
      <target href="m.capella#{node_target}"/>
    </ownedDiagramElements>
    <target href="m.capella#{UUID_B}"/>
  </diagram:DSemanticDiagram>""" if with_diagram else ""
    descriptor = (f'<ownedRepresentationDescriptors uid="{DESC}" name="Diag" repPath="#{REP}"/>'
                  if with_diagram else "")
    return f"""<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmlns:diagram="d">
  <viewpoint:DAnalysis xmlns:viewpoint="v" uid="_AAAAAAAAAAAAAAAAAAAAAA">
    <models href="m.capella#{UUID_A}"/>
    <other href="platform:/plugin/x/y.odesign#//@ownedViewpoints[name='a']"/>
    {descriptor}
  </viewpoint:DAnalysis>{diagram}
</xmi:XMI>
"""


def test_text_attrs_and_platform_hrefs_are_not_references(tmp_path):
    scan = mf.scan_model(_write(tmp_path / "m", CAPELLA, _aird()))
    targets = {r.target_id for r in scan.refs}
    assert UUID_GONE not in targets  # in a name="" and a platform:/ href only
    assert mf.dangling_refs(scan) == []
    multi = [r for r in scan.refs if r.attr == "multi"]
    assert {r.target_id for r in multi} == {UUID_A, UUID_B}
    assert {r.owner_id for r in multi} == {"aaaaaaaa-0000-0000-0000-0000000000ff"}


def test_diagram_descriptor_and_root_are_linked(tmp_path):
    scan = mf.scan_model(_write(tmp_path / "m", CAPELLA, _aird()))
    info = scan.diagrams[REP]
    assert (info.descriptor_uid, info.name, info.target_id, info.viewed_ids) == (DESC, "Diag", UUID_B, {UUID_A})


def test_deleted_diagram_is_reported(tmp_path):
    before = mf.scan_model(_write(tmp_path / "b", CAPELLA, _aird()))
    capella_without_b = re.sub(r'  <owned xsi:type="x:Capability".*?</owned>\n', "", CAPELLA, flags=re.S)
    capella_without_b = capella_without_b.replace(f" #{UUID_B}", "")
    after = mf.scan_model(_write(tmp_path / "a", capella_without_b, _aird(with_diagram=False)))
    diff = mf.compare_scans(before, after, UUID_B)
    assert diff["diagrams_deleted"] == [{"uid": DESC, "name": "Diag", "target_id": UUID_B}]
    assert {d["id"] for d in diff["removed_semantic"]} == {UUID_B, UUID_C}
    assert diff["new_dangling"] == []
    assert diff["target_defined_after"] is False


def test_preexisting_dangling_is_not_blamed_on_the_delete(tmp_path):
    broken = _aird(node_target=UUID_GONE)  # already dangling before
    before = mf.scan_model(_write(tmp_path / "b", CAPELLA, broken))
    after = mf.scan_model(_write(tmp_path / "a", CAPELLA, broken))
    diff = mf.compare_scans(before, after)
    assert diff["dangling_before_count"] == 1
    assert diff["dangling_after_count"] == 1
    assert diff["new_dangling"] == []
    assert "target_defined_after" not in diff


def test_added_ids_are_reported(tmp_path):
    before = mf.scan_model(_write(tmp_path / "b", CAPELLA, _aird()))
    extra = CAPELLA.replace("</root>", f'  <owned xsi:type="x:Entity" id="{UUID_GONE}" name="new"/>\n</root>')
    after = mf.scan_model(_write(tmp_path / "a", extra, _aird()))
    diff = mf.compare_scans(before, after)
    assert [d["id"] for d in diff["added_semantic"]] == [UUID_GONE]
    assert diff["removed_semantic"] == []
