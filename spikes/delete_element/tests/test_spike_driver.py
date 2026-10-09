"""run_spike end to end with a fake bridge: proves the driver plumbing
(seed once, fresh copy per run, file diffs, unavailable short-circuit,
results.json/md, fixture never touched) without Capella. It says nothing
about what Capella's delete really does -- that is the spike's job."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest
import run_spike
from _xmi_edit import drop_aird_node, drop_capella_element
from capella_mcp import bridge

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "car_hmi"
MONITORAR = "b2fd6238-ce55-4bdd-af1a-3779d1570bd2"


def _digest(root: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir()) if p.is_file()}


class FakeCapella:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def _model_dir(self, params) -> Path:
        rel = params["model_workspace_path"].split("/", 2)[2]  # "/models/<rel>"
        return (bridge.MODELS_ROOT / rel).parent

    def run_script(self, body: str, timeout: float = 0) -> dict:
        params = ast.literal_eval(body.splitlines()[0].split("=", 1)[1].strip())
        if '"probe": "seed"' in body:
            self.calls.append(("seed", params))
            return {"probe": "seed", "steps": [{"step": "x", "ok": True}], "saved": True}
        if '"probe": "verify"' in body:
            self.calls.append(("verify", params))
            return {"probe": "verify", "target_found": False, "still_present": [], "refresh": []}
        self.calls.append(("delete", params))
        name = params["mechanism_name"]
        if name == "delete_structure_in_tx":
            return {"probe": "delete", "delete_ok": False, "mechanism_unavailable": True,
                    "delete_exception": "bundle not found"}
        model_dir = self._model_dir(params)
        drop_capella_element(model_dir, params["element_id"])
        if name == "capella_cmd_in_tx":
            drop_aird_node(model_dir, params["element_id"])
        return {"probe": "delete", "delete_ok": True, "target_detached": True,
                "env": {"thread": "worker", "on_ui_thread": False, "workbench_running": False},
                "in_memory_dangling": [], "stages": [{"stage": "opened"}, {"stage": "saved"}]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fc = FakeCapella()
    monkeypatch.setattr(bridge, "_run_script", fc.run_script)
    monkeypatch.setattr(bridge, "create_element", lambda *a, **k: {"id": "aaaaaaaa-0000-0000-0000-0000000000fe"})
    for fn in ("create_container_diagram", "create_capability_diagram", "create_diagram"):
        monkeypatch.setattr(bridge, fn, lambda *a, **k: {"diagram_uid": "_x"})

    def export(mp):
        out = bridge.MODELS_ROOT / "png"
        out.mkdir(exist_ok=True)
        (out / "d.png").write_bytes(b"x" * 125)
        return {"files": [str(out / "d.png")]}

    monkeypatch.setattr(bridge, "export_diagram", export)
    monkeypatch.setattr(run_spike.shutil, "which", lambda name: "/usr/bin/" + name)
    capella = tmp_path / "capella" / "capella"
    capella.parent.mkdir()
    capella.write_text("#!/bin/sh\n")
    return fc, capella


def test_run_records_clean_vs_raw_delete_and_never_touches_the_fixture(fake, tmp_path):
    fc, capella = fake
    before = _digest(FIXTURE)
    out = tmp_path / "out"
    rc = run_spike.main(["run", "--capella-bin", str(capella), "--out", str(out),
                         "--mechanisms", "capella_cmd_in_tx,ecoreutil_delete,delete_structure_in_tx",
                         "--cases", "oa_leaf_in_oabd,actor"])
    assert rc == 0
    assert _digest(FIXTURE) == before

    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    runs = {(r["mechanism"], r["case"]): r for r in results["runs"]}
    assert len(runs) == 6
    assert [c for c, _p in fc.calls].count("seed") == 1

    clean = runs[("capella_cmd_in_tx", "oa_leaf_in_oabd")]
    assert clean["outcome"] == "deleted"
    assert clean["files_after_delete"]["new_dangling"] == []
    assert clean["files_after_delete"]["target_defined_after"] is False
    assert clean["export"]["files"][0]["blank"] is True
    assert clean["verify"]["target_found"] is False

    raw = runs[("ecoreutil_delete", "oa_leaf_in_oabd")]
    assert raw["outcome"] == "deleted"
    assert len(raw["files_after_delete"]["new_dangling"]) == 2
    assert raw["target_occurrences_after_delete"]

    # each run got its own copy: the raw delete didn't see the clean one's edits
    assert raw["views_before"] == clean["views_before"] != []

    assert runs[("delete_structure_in_tx", "oa_leaf_in_oabd")]["outcome"] == "unavailable"
    assert runs[("delete_structure_in_tx", "actor")]["outcome"] == "skipped_unavailable"

    md = (out / "results.md").read_text(encoding="utf-8")
    assert "| capella_cmd_in_tx | oa_leaf_in_oabd | deleted |" in md
    assert (out / "seeded" / "seed_info.json").exists()
    assert (out / "runs" / "capella_cmd_in_tx__oa_leaf_in_oabd" / "run.json").exists()


def test_actor_case_on_a_non_ownedFunctions_element_is_reported_not_crashing(fake, tmp_path):
    """The fake can't text-delete an Entity (regex targets ownedFunctions):
    its AssertionError must surface as a driver_error in the record, not
    abort the whole matrix."""
    fc, capella = fake
    out = tmp_path / "out"
    run_spike.main(["run", "--capella-bin", str(capella), "--out", str(out),
                    "--mechanisms", "ecoreutil_delete", "--cases", "actor,oa_leaf_in_oabd", "--no-export"])
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    outcomes = {r["case"]: r["outcome"] for r in results["runs"]}
    assert outcomes == {"actor": "driver_error", "oa_leaf_in_oabd": "deleted"}


def test_seed_is_reused_unless_reseed(fake, tmp_path):
    fc, capella = fake
    out = tmp_path / "out"
    args = ["run", "--capella-bin", str(capella), "--out", str(out), "--mechanisms", "capella_cmd_in_tx",
            "--cases", "oa_leaf_in_oabd", "--no-verify", "--no-export"]
    run_spike.main(args)
    run_spike.main(args)
    assert [c for c, _p in fc.calls].count("seed") == 1
    run_spike.main(args + ["--reseed"])
    assert [c for c, _p in fc.calls].count("seed") == 2


def test_quick_plan_and_unknown_names(fake, tmp_path):
    fc, capella = fake
    with pytest.raises(SystemExit):
        run_spike.main(["run", "--capella-bin", str(capella), "--out", str(tmp_path / "o"),
                        "--mechanisms", "nope"])
    run_spike.main(["run", "--capella-bin", str(capella), "--out", str(tmp_path / "q"), "--quick",
                    "--no-export"])
    results = json.loads((tmp_path / "q" / "results.json").read_text(encoding="utf-8"))
    assert [(r["mechanism"], r["case"]) for r in results["runs"]] == [
        ("capella_cmd_in_tx", "oa_leaf_in_oabd"), ("ecoreutil_delete", "oa_leaf_in_oabd")]
