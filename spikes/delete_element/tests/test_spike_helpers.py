"""discover / candidates / report / probe: pure parts of the spike kit."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import candidates
import discover
import probe
import pytest
import report

MANIFEST = (
    "Manifest-Version: 1.0\n"
    "Bundle-SymbolicName: org.polarsys.capella.core.platform.sirius.ui.com\n"
    " mands;singleton:=true\n"
    "Bundle-Version: 7.0.1.202503211540\n"
)


def _jar(path: Path, manifest: str, classes: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", manifest)
        for c in classes:
            z.writestr(c.replace(".", "/") + ".class", b"\xca\xfe\xba\xbe")
    return path


@pytest.fixture
def plugins(tmp_path) -> Path:
    d = tmp_path / "plugins"
    d.mkdir()
    _jar(d / "org.polarsys.capella.core.platform.sirius.ui.commands_7.0.1.202503211540.jar", MANIFEST, [
        "org.polarsys.capella.core.platform.sirius.ui.commands.CapellaDeleteCommand",
        "org.polarsys.capella.core.platform.sirius.ui.commands.CapellaDeleteCommand$Inner",
        "org.polarsys.capella.core.platform.sirius.ui.commands.Other",
    ])
    _jar(d / "org.eclipse.jface_3.0.jar", "Bundle-SymbolicName: org.eclipse.jface\n",
         ["org.eclipse.jface.DeleteThing"])  # prefix not scanned
    dir_bundle = d / "org.polarsys.capella.core.model.handler_7.0.1"
    (dir_bundle / "META-INF").mkdir(parents=True)
    (dir_bundle / "META-INF" / "MANIFEST.MF").write_text(
        "Bundle-SymbolicName: org.polarsys.capella.core.model.handler\n", encoding="utf-8")
    cls = dir_bundle / "bin" / "org" / "polarsys" / "capella" / "core" / "model" / "handler" / "cmd"
    cls.mkdir(parents=True)
    (cls / "DeleteStructureCommand.class").write_bytes(b"\xca\xfe")
    return d


def test_discover_lists_delete_classes_with_bundle_names(plugins):
    found = discover.discover(plugins)
    pairs = {(f["bundle"], f["class"]) for f in found}
    assert pairs == {
        ("org.polarsys.capella.core.platform.sirius.ui.commands",
         "org.polarsys.capella.core.platform.sirius.ui.commands.CapellaDeleteCommand"),
        ("org.polarsys.capella.core.model.handler",
         "org.polarsys.capella.core.model.handler.cmd.DeleteStructureCommand"),
    }


def test_capella_version_from_bundle_file_name(plugins, tmp_path):
    assert discover.capella_version(plugins) == "7.0.1"  # from the model.handler dir bundle
    assert discover.capella_version(tmp_path / "missing") is None


def test_resolve_candidates_found_corrected_and_missing(plugins):
    resolved, notes = candidates.resolve_candidates(candidates.MECHANISMS, discover.discover(plugins))
    assert resolved["capella_cmd_in_tx"]["discovery"] == "found"
    # package differs from the guess -> class corrected, bundle kept
    ds = resolved["delete_structure_in_tx"]
    assert ds["discovery"] == "class_corrected"
    assert ds["class"] == "org.polarsys.capella.core.model.handler.cmd.DeleteStructureCommand"
    assert resolved["emf_delete_command_in_tx"]["discovery"] == "not_found"
    assert resolved["sirius_util_delete_in_tx"]["discovery"] == "not_scanned"
    assert "discovery" not in resolved["ecoreutil_delete"]
    assert any("DeleteStructureCommand" in n for n in notes)
    # the module-level table is not mutated
    assert "discovery" not in candidates.MECHANISMS["capella_cmd_in_tx"]


def test_resolve_candidates_bundle_corrected():
    found = [{"bundle": "other.bundle", "class": candidates.MECHANISMS["capella_cmd_em"]["class"], "source": "x"}]
    resolved, notes = candidates.resolve_candidates(candidates.MECHANISMS, found)
    assert resolved["capella_cmd_em"]["bundle"] == "other.bundle"
    assert resolved["capella_cmd_em"]["discovery"] == "bundle_corrected"


def test_resolve_candidates_without_discovery_is_identity():
    resolved, notes = candidates.resolve_candidates(candidates.MECHANISMS, None)
    assert resolved == candidates.MECHANISMS and notes


def test_default_plan_shape():
    plan = candidates.default_plan()
    assert len(plan) == len(candidates.PRIMARY) * len(candidates.CASES) + len(candidates.CONTROLS)
    assert ("ecoreutil_delete", "oa_leaf_in_oabd") in plan
    assert ("ecoreutil_delete", "actor") not in plan
    assert candidates.default_plan(["ecoreutil_delete"], ["actor"]) == [("ecoreutil_delete", "actor")]
    assert len(candidates.default_plan(cases=["actor"])) == len(candidates.MECHANISMS)


def test_cases_cover_the_first_release_types():
    types = {c["type"] for c in candidates.CASES.values()}
    assert {"OperationalActivity", "OperationalEntity", "OperationalActor", "OperationalCapability",
            "FunctionalExchange"} <= types


PARAMS = {
    "model_workspace_path": "/models/car_hmi/car_hmi.aird",
    "element_id": "b2fd6238-ce55-4bdd-af1a-3779d1570bd2",
    "case": "oa_leaf_in_oabd",
    "mechanism_name": "capella_cmd_in_tx",
    "mechanism": candidates.MECHANISMS["capella_cmd_in_tx"],
    "em_lookups": candidates.EXECUTION_MANAGER_LOOKUPS,
    "progress_path": "/tmp/x.json",
    "removed_ids": ["a"],
    "ids": {"capability": "c"},
}


@pytest.mark.parametrize("builder", [probe.delete_body, probe.verify_body, probe.seed_body])
def test_probe_bodies_compile_and_carry_params_as_one_literal(builder):
    body = builder(PARAMS)
    compile(body, "<probe>", "exec")
    first = body.splitlines()[0]
    assert first.startswith("_PARAMS = {")
    # every body writes exactly one result, and never a top-level "error"
    # key (that would make bridge._run_script raise and drop the diagnostics)
    assert body.count("_write_result(_result)") == 1
    assert not re.search(r'(_result|out) = \{"error"|_write_result\(\{"error"', body)


def test_probe_params_cannot_inject_code():
    evil = dict(PARAMS, element_id='x"); import os; os.system("boom"); ("')
    body = probe.delete_body(evil)
    ns: dict = {}
    exec(body.splitlines()[0], ns)  # only the params line: must be a plain dict literal
    assert ns["_PARAMS"]["element_id"] == evil["element_id"]


def test_classify_outcome():
    assert report.classify_outcome(None) == "not_run"
    assert report.classify_outcome({"driver_error": "Capella headless call timed out after 300s"}) == "timeout"
    assert report.classify_outcome({"driver_error": "boom"}) == "driver_error"
    assert report.classify_outcome({"probe_error": "element not found: x"}) == "not_found"
    assert report.classify_outcome({"probe_error": "other"}) == "probe_error"
    assert report.classify_outcome({"delete_ok": False, "mechanism_unavailable": True}) == "unavailable"
    assert report.classify_outcome({"delete_ok": False}) == "exception"
    assert report.classify_outcome({"delete_ok": True, "target_detached": False}) == "no_effect"
    assert report.classify_outcome({"delete_ok": True, "target_detached": True}) == "deleted"


def test_summary_and_markdown():
    rec = {
        "mechanism": "capella_cmd_in_tx", "case": "oa_leaf_in_oabd", "element_id": "e",
        "views_before": [{"uid": "u", "name": "OABD", "rooted": False, "shown": True}],
        "delete": {"delete_ok": True, "target_detached": True, "env": {"on_ui_thread": False, "thread": "w"},
                   "in_memory_dangling": [], "stages": [{"stage": "opened"}, {"stage": "saved"}]},
        "files_after_delete": {"removed_semantic_count": 3, "removed_semantic": [], "new_dangling": [],
                               "target_defined_after": False, "target_shown_in_diagrams_after": [],
                               "diagrams_deleted": [{"name": "Rooted"}]},
        "target_occurrences_after_delete": [],
        "export": {"files": [{"name": "a.png", "size": 125, "blank": True}, {"name": "b.png", "size": 9000}]},
        "seconds": 12.5,
    }
    row = report.summarize_run(rec)
    assert row["outcome"] == "deleted"
    assert row["target_gone_from_files"] is True
    assert row["removed_semantic"] == 3
    assert row["diagrams_deleted"] == ["Rooted"]
    assert row["blank_exports"] == 1 and row["exports"] == 2
    assert row["shown_in_before"] == ["OABD"]

    md = report.render_markdown({"meta": {"capella_version": "7.0.1"}, "runs": [rec]})
    assert "| capella_cmd_in_tx | oa_leaf_in_oabd | deleted | no |" in md
    assert "### capella_cmd_in_tx / oa_leaf_in_oabd" in md
    assert "opened > saved" in md
