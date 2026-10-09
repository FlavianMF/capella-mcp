"""delete_element phase 1 spike driver (throwaway, not part of the server).

Runs candidate Capella delete mechanisms against COPIES of the car_hmi
fixture and records what each one did. Guide, commands and the results
template: docs/spikes/delete-element-spike.md.

Subcommands (run from the repo root, with the repo's deps, e.g. ``uv run``):

    discover        list *Delete* classes in the Capella install (no Capella process)
    run             seed a fixture copy, then delete per (mechanism, case), each on a fresh copy
    attach-prepare  seed a copy and lay it out as an Eclipse project for the live GUI
    attach-run      send one delete probe to a live Capella GUI (attach listener) and record it

It drives Capella through capella_mcp.bridge's own private plumbing
(``_run_script``, ``_run_script_attach``, ``_workspace_path_for_model``) so
the spike exercises exactly the process model the real tool will use.
The original fixture is only ever read (copied with shutil.copytree).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT / "src"))

import candidates  # noqa: E402
import discover as discover_mod  # noqa: E402
import model_files  # noqa: E402
import probe  # noqa: E402
import report  # noqa: E402

DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "car_hmi"
MODEL_FILE = "car_hmi.aird"
ATTACH_PROJECT = "delete_spike_attach"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _bridge():
    from capella_mcp import bridge  # imported lazily: needs the repo deps

    return bridge


def _configure_bridge(capella_bin: Path, out: Path):
    bridge = _bridge()
    bridge.CAPELLA_BIN = str(capella_bin)
    bridge.WORKSPACE_ROOT = (out / "workspaces").resolve()
    bridge._python4capella_project_dir = None
    return bridge


def _resolve_capella_bin(arg: str | None) -> Path:
    raw = arg or os.environ.get("CAPELLA_BIN") or "/opt/capella/capella"
    path = Path(raw).expanduser()
    if not path.exists():
        sys.exit(f"Capella binary not found: {path}. Pass --capella-bin or set CAPELLA_BIN.")
    return path.resolve()


def _plugins_dir(capella_bin: Path) -> Path:
    return capella_bin.parent / "plugins"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _write_results(out: Path, results: dict, stem: str = "results") -> None:
    _write_json(out / f"{stem}.json", results)
    (out / f"{stem}.md").write_text(report.render_markdown(results), encoding="utf-8")


def _copy_fixture(src: Path, dest_models_root: Path) -> Path:
    """Copy the fixture directory to <dest_models_root>/car_hmi and return
    the copied .aird path. Never writes to ``src``."""
    dest = dest_models_root / src.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.lock", "*_diagram_exports"))
    return dest / MODEL_FILE


# --------------------------------------------------------------------------- discovery

def cmd_discover(args) -> int:
    capella_bin = _resolve_capella_bin(args.capella_bin)
    found = discover_mod.discover(_plugins_dir(capella_bin))
    out = Path(args.out) if args.out else None
    if out:
        _write_json(out / "discovery.json", found)
    resolved, notes = candidates.resolve_candidates(candidates.MECHANISMS, found)
    print(json.dumps({"capella_version": discover_mod.capella_version(_plugins_dir(capella_bin)),
                      "classes": len(found), "notes": notes,
                      "mechanisms": {k: {"bundle": v["bundle"], "class": v["class"], "discovery": v.get("discovery")}
                                     for k, v in resolved.items()}}, indent=2))
    return 0


def _load_mechanisms(args, capella_bin: Path, out: Path) -> tuple[dict, list[str]]:
    mechanisms = candidates.MECHANISMS
    if args.candidates:
        mechanisms = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    try:
        found = discover_mod.discover(_plugins_dir(capella_bin))
        _write_json(out / "discovery.json", found)
    except OSError as exc:
        found = None
        print(f"[spike] discovery failed ({exc}); using candidates as written", flush=True)
    return candidates.resolve_candidates(mechanisms, found)


# --------------------------------------------------------------------------- seeding

def _seed(bridge, out: Path, fixture: Path, timeout: float, reseed: bool) -> tuple[Path, dict]:
    """Seed a fixture copy once with what car_hmi's OA layer lacks:
    capability involvements, an activity allocation, one interaction, and
    OAIB/OEB/OCB/OABD diagrams that show the targets. Every later run
    copies this seeded model."""
    seeded_root = (out / "seeded" / "models").resolve()
    info_path = out / "seeded" / "seed_info.json"
    if info_path.exists() and not reseed:
        return seeded_root, json.loads(info_path.read_text(encoding="utf-8"))

    print("[spike] seeding a fixture copy (several Capella launches)...", flush=True)
    seeded_root.mkdir(parents=True, exist_ok=True)
    aird = _copy_fixture(fixture, seeded_root)
    bridge.MODELS_ROOT = seeded_root
    mp = f"{fixture.name}/{MODEL_FILE}"
    ids = {name: case["element_id"] for name, case in candidates.CASES.items()
           if not case["element_id"].startswith("seed:")}
    info: dict = {"steps": [], "ids": {}, "fixture_dir": fixture.name}

    def step(name, fn):
        t0 = time.time()
        try:
            res = fn()
            info["steps"].append({"step": name, "ok": True, "result": res, "seconds": round(time.time() - t0, 1)})
            return res
        except Exception as exc:  # recorded, not fatal: dependent cases report it
            info["steps"].append({"step": name, "ok": False, "error": str(exc)[:3000],
                                  "seconds": round(time.time() - t0, 1)})
            return None

    params = {"model_workspace_path": bridge._workspace_path_for_model(aird), "ids": ids,
              "progress_path": str(out / "seeded" / "seed.progress.json")}
    step("involvements_and_allocation", lambda: bridge._run_script(probe.seed_body(params), timeout=timeout))
    fe = step("functional_exchange", lambda: bridge.create_element(
        mp, "oa", "FunctionalExchange", candidates.CASES["functional_exchange"]["label"],
        parent_id=ids["oa_with_children"],
        attributes={"source_id": ids["oa_leaf_in_oabd"], "target_id": candidates.FE_TARGET_ID},
    ))
    if fe and fe.get("id"):
        info["ids"]["functional_exchange"] = fe["id"]
    step("oaib", lambda: bridge.create_container_diagram(mp, "oa", "OperationalActivity", diagram_name="Spike OAIB"))
    step("oeb", lambda: bridge.create_container_diagram(mp, "oa", "OperationalEntity", diagram_name="Spike OEB"))
    step("ocb", lambda: bridge.create_capability_diagram(mp, diagram_name="Spike OCB"))
    step("oabd_rooted_at_leaf", lambda: bridge.create_diagram(
        mp, "oa", "OperationalActivity", root_id=ids["oa_leaf_in_oabd"], diagram_name="Spike OABD rooted at Monitorar"))
    step("oebd_rooted_at_entity", lambda: bridge.create_diagram(
        mp, "oa", "OperationalEntity", root_id=ids["entity_with_child"], diagram_name="Spike OEBD rooted at Veiculo"))

    scan = model_files.scan_model(aird.parent)
    info["dangling_after_seed"] = len(model_files.dangling_refs(scan))
    info["diagrams_after_seed"] = sorted(d.name or uid for uid, d in scan.diagrams.items())
    _write_json(info_path, info)
    for s in info["steps"]:
        print(f"[spike]   seed {s['step']}: {'ok' if s['ok'] else 'FAILED ' + s['error'][:200]}", flush=True)
    return seeded_root, info


def _element_id(case: dict, seed_info: dict) -> str | None:
    eid = case["element_id"]
    if eid.startswith("seed:"):
        return seed_info.get("ids", {}).get(eid.split(":", 1)[1])
    return eid


# --------------------------------------------------------------------------- one run

def _export(bridge, mp: str) -> dict:
    try:
        res = bridge.export_diagram(mp)
    except Exception as exc:
        return {"error": str(exc)[:2000], "files": []}
    files = []
    for f in res.get("files", []):
        size = Path(f).stat().st_size
        files.append({"name": Path(f).name, "size": size, "blank": size <= report.BLANK_PNG_MAX_BYTES})
    return {"files": files}


def run_one(bridge, out: Path, seeded_root: Path, seed_info: dict, mech_name: str, mech: dict,
            case_name: str, args) -> dict:
    case = candidates.CASES[case_name]
    rec: dict = {"mechanism": mech_name, "case": case_name, "case_info": case,
                 "mechanism_def": mech, "started": _now()}
    element_id = _element_id(case, seed_info)
    rec["element_id"] = element_id
    if element_id is None:
        rec["outcome"] = "seed_missing"
        return rec

    t0 = time.time()
    run_dir = (out / "runs" / f"{mech_name}__{case_name}").resolve()
    if run_dir.exists():
        shutil.rmtree(run_dir)
    models_root = run_dir / "models"
    models_root.mkdir(parents=True)
    aird = _copy_fixture(seeded_root / seed_info.get("fixture_dir", DEFAULT_FIXTURE.name), models_root)
    bridge.MODELS_ROOT = models_root
    mp = f"{aird.parent.name}/{MODEL_FILE}"

    before = model_files.scan_model(aird.parent)
    rec["views_before"] = model_files.views_of(before, element_id)

    params = {
        "model_workspace_path": bridge._workspace_path_for_model(aird),
        "element_id": element_id, "case": case_name,
        "mechanism_name": mech_name, "mechanism": mech,
        "em_lookups": candidates.EXECUTION_MANAGER_LOOKUPS,
        "progress_path": str(run_dir / "delete.progress.json"),
    }
    try:
        delete = bridge._run_script(probe.delete_body(params), timeout=args.timeout)
    except Exception as exc:
        delete = {"driver_error": str(exc)[:4000]}
        progress = run_dir / "delete.progress.json"
        if progress.exists():
            delete["progress"] = json.loads(progress.read_text(encoding="utf-8"))
    rec["delete"] = delete
    rec["outcome"] = report.classify_outcome(delete)

    after = model_files.scan_model(aird.parent)
    rec["files_after_delete"] = model_files.compare_scans(before, after, element_id)
    rec["target_occurrences_after_delete"] = model_files.find_occurrences(aird.parent, element_id)

    if rec["outcome"] == "deleted" and not args.no_verify:
        removed_ids = [d["id"] for d in rec["files_after_delete"]["removed_semantic"]]
        vparams = {"model_workspace_path": params["model_workspace_path"], "element_id": element_id,
                   "removed_ids": removed_ids, "refresh": True, "validate": True,
                   "progress_path": str(run_dir / "verify.progress.json")}
        try:
            rec["verify"] = bridge._run_script(probe.verify_body(vparams), timeout=args.timeout)
        except Exception as exc:
            rec["verify"] = {"driver_error": str(exc)[:4000]}
        after_v = model_files.scan_model(aird.parent)
        rec["files_after_verify"] = model_files.compare_scans(before, after_v, element_id)
        rec["target_occurrences_after_verify"] = model_files.find_occurrences(aird.parent, element_id)
        if not args.no_export:
            rec["export"] = _export(bridge, mp)
    rec["seconds"] = round(time.time() - t0, 1)
    _write_json(run_dir / "run.json", rec)
    return rec


def cmd_run(args) -> int:
    capella_bin = _resolve_capella_bin(args.capella_bin)
    if shutil.which("xvfb-run") is None:
        sys.exit("xvfb-run not found on PATH (the bridge launches Capella through it).")
    out = Path(args.out or REPO_ROOT / "spike-results" / "delete_element" /
               _dt.datetime.now().strftime("%Y%m%d-%H%M%S")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    fixture = Path(args.fixture).resolve()
    bridge = _configure_bridge(capella_bin, out)
    mechanisms, notes = _load_mechanisms(args, capella_bin, out)

    if args.quick:
        plan = list(candidates.QUICK_PLAN)
    else:
        plan = candidates.default_plan(_csv(args.mechanisms), _csv(args.cases))
    unknown = [p for p in plan if p[0] not in mechanisms or p[1] not in candidates.CASES]
    if unknown:
        sys.exit(f"unknown mechanism/case in plan: {unknown}")

    results = {"meta": {"started": _now(), "capella_bin": str(capella_bin),
                        "capella_version": discover_mod.capella_version(_plugins_dir(capella_bin)),
                        "fixture": str(fixture), "plan_size": len(plan), "attach": False,
                        "timeout_seconds": args.timeout, "out": str(out)},
               "discovery_notes": notes, "runs": []}
    print(f"[spike] results -> {out}", flush=True)
    for n in notes:
        print(f"[spike] discovery: {n}", flush=True)

    seeded_root, seed_info = _seed(bridge, out, fixture, args.timeout, args.reseed)
    results["seed"] = seed_info
    _write_results(out, results)

    unavailable: set[str] = set()
    for i, (mech_name, case_name) in enumerate(plan, 1):
        if mech_name in unavailable:
            results["runs"].append({"mechanism": mech_name, "case": case_name, "outcome": "skipped_unavailable"})
            continue
        print(f"[spike] ({i}/{len(plan)}) {mech_name} / {case_name} ...", flush=True)
        rec = run_one(bridge, out, seeded_root, seed_info, mech_name, mechanisms[mech_name], case_name, args)
        print(f"[spike]   -> {rec['outcome']} ({rec.get('seconds', '-')} s)", flush=True)
        if rec["outcome"] == "unavailable":
            unavailable.add(mech_name)
        results["runs"].append(rec)
        _write_results(out, results)

    results["meta"]["finished"] = _now()
    _write_results(out, results)
    print(f"[spike] done: {out / 'results.md'}", flush=True)
    return 0


# --------------------------------------------------------------------------- attach mode

def _attach_dir(out: Path) -> Path:
    return (out / "attach" / ATTACH_PROJECT).resolve()


def cmd_attach_prepare(args) -> int:
    capella_bin = _resolve_capella_bin(args.capella_bin)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    bridge = _configure_bridge(capella_bin, out)
    seeded_root, seed_info = _seed(bridge, out, Path(args.fixture).resolve(), args.timeout, args.reseed)
    project = _attach_dir(out)
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(seeded_root / seed_info.get("fixture_dir", DEFAULT_FIXTURE.name), project,
                    ignore=shutil.ignore_patterns("*.lock", "*_diagram_exports"))
    (project / ".project").write_text(bridge._PROJECT_DESCRIPTOR.format(name=ATTACH_PROJECT), encoding="utf-8")
    _write_json(out / "attach" / "seed_info.json", seed_info)
    print(f"""
Seeded copy ready as Eclipse project '{ATTACH_PROJECT}':
    {project}

In your Capella GUI (attach listener running, see docs/architecture.md):
  1. File > Import > General > Existing Projects into Workspace
     root directory: {project}
     leave "Copy projects into workspace" UNCHECKED (the path must stay the same)
  2. Open {MODEL_FILE} from that project (double-click), and open the diagram
     'Atividades de Velocidade do Veículo' so you can watch it.
  3. Check that ~/.capella-mcp/attach/*/heartbeat.json lists
     {project / MODEL_FILE}
  4. Run: uv run python spikes/delete_element/run_spike.py attach-run --out {out}
""")
    return 0


def _ask(prompt: str, no_prompt: bool) -> str | None:
    if no_prompt:
        return None
    try:
        return input(prompt + " ").strip()
    except EOFError:
        return None


def cmd_attach_run(args) -> int:
    out = Path(args.out).resolve()
    bridge = _bridge()
    aird = _attach_dir(out) / MODEL_FILE
    if not aird.exists():
        sys.exit(f"{aird} missing: run attach-prepare --out {out} first")
    seed_info = json.loads((out / "attach" / "seed_info.json").read_text(encoding="utf-8"))
    target = bridge._attach_target(aird)
    if target is None:
        sys.exit(f"no live Capella GUI reports {aird} as open (heartbeat under {bridge.ATTACH_ROOT}). "
                 "Open the model in the GUI with the attach listener running.")
    mech_name, case_name = args.mechanism, args.case
    mechanisms = candidates.MECHANISMS
    if args.candidates:
        mechanisms = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    mech = mechanisms[mech_name]
    case = candidates.CASES[case_name]
    element_id = _element_id(case, seed_info)
    before = model_files.scan_model(aird.parent)
    rec: dict = {"mechanism": mech_name, "case": case_name, "case_info": case, "element_id": element_id,
                 "attach": True, "listener_pid": target.pid, "started": _now(),
                 "views_before": model_files.views_of(before, element_id)}
    params = {"model_workspace_path": "/ignored-in-attach-mode", "element_id": element_id, "case": case_name,
              "mechanism_name": mech_name, "mechanism": mech,
              "em_lookups": candidates.EXECUTION_MANAGER_LOOKUPS,
              "progress_path": str(out / "attach" / f"{mech_name}__{case_name}.progress.json")}
    print(f"[spike] sending {mech_name} / {case_name} to the GUI (pid {target.pid}); watch for dialogs...", flush=True)
    t0 = time.time()
    try:
        delete = bridge._run_script_attach(probe.delete_body(params), target, aird, timeout=args.timeout)
    except Exception as exc:  # _AttachUnavailable = listener did not answer (blocked?)
        delete = {"driver_error": f"{type(exc).__name__}: timed out or failed: {exc}"[:4000]}
        progress = Path(params["progress_path"])
        if progress.exists():
            delete["progress"] = json.loads(progress.read_text(encoding="utf-8"))
    rec["delete"] = delete
    rec["outcome"] = report.classify_outcome(delete)
    rec["seconds"] = round(time.time() - t0, 1)
    print(f"[spike]   -> {rec['outcome']} in {rec['seconds']} s", flush=True)

    obs = {}
    obs["dialog_appeared"] = _ask("Did any dialog appear in the GUI during the call? (y/n, what):", args.no_prompt)
    obs["gui_updated"] = _ask("Is the element gone from the Project Explorer and the open diagram? (y/n):",
                              args.no_prompt)
    obs["undo_offered"] = _ask("Does Edit > Undo offer to undo the delete? Do NOT click it. (y/n, label):",
                               args.no_prompt)
    obs["editor_dirty"] = _ask("Is the model marked dirty (*) / unsaved? (y/n):", args.no_prompt)
    _ask("Now save in Capella (Ctrl+S), then press Enter here.", args.no_prompt)
    after = model_files.scan_model(aird.parent)
    rec["files_after_delete"] = model_files.compare_scans(before, after, element_id)
    rec["target_occurrences_after_delete"] = model_files.find_occurrences(aird.parent, element_id)
    obs["after_save_errors"] = _ask("Any error/warning on save or in the Error Log view? (y/n, what):",
                                    args.no_prompt)
    rec["attach_observations"] = obs

    results_path = out / "attach_results.json"
    results = (json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists()
               else {"meta": {"attach": True, "started": _now()}, "runs": []})
    results["runs"].append(rec)
    results["meta"]["finished"] = _now()
    _write_results(out, results, stem="attach_results")
    print(f"[spike] recorded in {out / 'attach_results.md'}", flush=True)
    return 0


# --------------------------------------------------------------------------- CLI

def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, needs_out=False):
        p.add_argument("--capella-bin", help="Capella launcher (default: $CAPELLA_BIN, then /opt/capella/capella)")
        p.add_argument("--out", required=needs_out, help="results directory")
        p.add_argument("--timeout", type=float, default=300.0, help="per Capella call, seconds (default 300)")

    p = sub.add_parser("discover", help="list delete classes in the install")
    common(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("run", help="run the spike matrix headless")
    common(p)
    p.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="fixture dir (copied, never modified)")
    p.add_argument("--quick", action="store_true", help="only capella_cmd_in_tx + ecoreutil on the issue #4 case")
    p.add_argument("--mechanisms", help="comma list (default: primaries on all cases, controls on one)")
    p.add_argument("--cases", help="comma list of case names")
    p.add_argument("--candidates", help="JSON file replacing candidates.MECHANISMS")
    p.add_argument("--no-verify", action="store_true", help="skip the reopen/refresh/validate pass")
    p.add_argument("--no-export", action="store_true", help="skip PNG export after each delete")
    p.add_argument("--reseed", action="store_true", help="rebuild the seeded copy even if --out has one")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("attach-prepare", help="seed a copy as an Eclipse project for the GUI")
    common(p, needs_out=True)
    p.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    p.add_argument("--reseed", action="store_true")
    p.set_defaults(func=cmd_attach_prepare)

    p = sub.add_parser("attach-run", help="delete through the live GUI's attach listener")
    common(p, needs_out=True)
    p.add_argument("--mechanism", default="capella_cmd_in_tx")
    p.add_argument("--case", default="oa_leaf_in_oabd")
    p.add_argument("--candidates")
    p.add_argument("--no-prompt", action="store_true")
    p.set_defaults(func=cmd_attach_run, timeout=120.0)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
