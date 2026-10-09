"""Turn the spike's results.json into a summary table + markdown report.

Pure (no Capella): ``python spikes/delete_element/report.py results.json``
re-renders ``results.md`` from a results file, e.g. one pasted back by the
user.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BLANK_PNG_MAX_BYTES = 200  # ~125 bytes is the known blank export (tests/test_integration.py)


def classify_outcome(delete: dict | None) -> str:
    """One word for what the delete probe reported."""
    if not delete:
        return "not_run"
    if "driver_error" in delete:
        return "timeout" if "timed out" in delete["driver_error"] else "driver_error"
    if "probe_error" in delete:
        return "not_found" if "element not found" in delete["probe_error"] else "probe_error"
    if delete.get("mechanism_unavailable"):
        return "unavailable"
    if not delete.get("delete_ok"):
        return "exception"
    if not delete.get("target_detached"):
        return "no_effect"
    return "deleted"


def summarize_run(rec: dict) -> dict:
    delete = rec.get("delete") or {}
    env = delete.get("env") or {}
    fd = rec.get("files_after_delete") or {}
    fv = rec.get("files_after_verify") or {}
    verify = rec.get("verify") or {}
    shown_before = sorted(v["name"] for v in rec.get("views_before", []) if v.get("shown"))
    rooted_before = sorted(v["name"] for v in rec.get("views_before", []) if v.get("rooted"))
    final = fv or fd
    exports = (rec.get("export") or {}).get("files") or []
    return {
        "mechanism": rec.get("mechanism"),
        "case": rec.get("case"),
        "outcome": rec.get("outcome") or classify_outcome(delete),
        "thread": env.get("thread"),
        "on_ui_thread": env.get("on_ui_thread"),
        "workbench_running": env.get("workbench_running"),
        "removed_semantic": fd.get("removed_semantic_count"),
        "target_gone_from_files": (
            None if not fd else (not fd.get("target_defined_after") and not rec.get("target_occurrences_after_delete"))
        ),
        "new_dangling_after_delete": len(fd.get("new_dangling", [])) if fd else None,
        "in_memory_dangling": len(delete.get("in_memory_dangling", [])) if "in_memory_dangling" in delete else None,
        "new_dangling_after_verify": len(fv.get("new_dangling", [])) if fv else None,
        "diagrams_deleted": [d.get("name") for d in final.get("diagrams_deleted", [])],
        "diagrams_rooted_before": rooted_before,
        "shown_in_before": shown_before,
        "still_shown_after_delete": fd.get("target_shown_in_diagrams_after"),
        "still_shown_after_verify": fv.get("target_shown_in_diagrams_after") if fv else None,
        "reopen_target_found": verify.get("target_found") if verify else None,
        "emf_validation_errors": ((verify.get("emf_validation") or {}).get("error_count")) if verify else None,
        "blank_exports": sum(1 for f in exports if f.get("blank")),
        "exports": len(exports),
        "seconds": rec.get("seconds"),
    }


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "none"
    return str(v)


SUMMARY_COLUMNS = [
    ("mechanism", "mechanism"),
    ("case", "case"),
    ("outcome", "outcome"),
    ("on_ui_thread", "UI thread"),
    ("workbench_running", "workbench"),
    ("removed_semantic", "removed (.capella ids)"),
    ("target_gone_from_files", "uuid gone from files"),
    ("new_dangling_after_delete", "new dangling refs (after delete+save)"),
    ("new_dangling_after_verify", "new dangling refs (after reopen+refresh+save)"),
    ("still_shown_after_delete", "still shown in (after delete)"),
    ("still_shown_after_verify", "still shown in (after refresh)"),
    ("diagrams_deleted", "diagrams deleted"),
    ("blank_exports", "blank PNGs"),
    ("seconds", "s"),
]


def render_markdown(results: dict) -> str:
    runs = results.get("runs", [])
    lines = ["# delete_element spike results", ""]
    meta = results.get("meta", {})
    for key in ("started", "finished", "capella_bin", "capella_version", "fixture", "plan_size", "attach"):
        if key in meta:
            lines.append(f"- **{key}**: {meta[key]}")
    for note in results.get("discovery_notes", []):
        lines.append(f"- discovery: {note}")
    seed = results.get("seed")
    if seed:
        lines.append(f"- seed steps: {json.dumps(seed.get('steps', seed), ensure_ascii=False)[:1500]}")
    lines += ["", "## Summary", ""]
    lines.append("| " + " | ".join(h for _k, h in SUMMARY_COLUMNS) + " |")
    lines.append("|" + "---|" * len(SUMMARY_COLUMNS))
    for rec in runs:
        row = summarize_run(rec)
        lines.append("| " + " | ".join(_fmt(row[k]) for k, _h in SUMMARY_COLUMNS) + " |")
    lines += ["", "## Runs", ""]
    for rec in runs:
        lines += _render_run(rec)
    return "\n".join(lines) + "\n"


def _render_run(rec: dict) -> list[str]:
    delete = rec.get("delete") or {}
    out = [f"### {rec.get('mechanism')} / {rec.get('case')}", ""]
    out.append(f"- outcome: **{rec.get('outcome') or classify_outcome(delete)}**, element `{rec.get('element_id')}`")
    info = delete.get("mechanism_info") or {}
    for key in ("class_loaded", "execution_manager", "constructor", "bool_slot_fields", "bool_values",
                "forced_confirm_false", "can_execute", "committed", "rolled_back"):
        if key in info:
            out.append(f"- {key}: `{json.dumps(info[key], ensure_ascii=False)}`")
    if delete.get("env"):
        out.append(f"- env: `{json.dumps(delete['env'])}`")
    if "delete_seconds" in delete:
        out.append(f"- delete call: {delete['delete_seconds']} s")
    for key in ("delete_exception", "probe_error", "driver_error", "save_error"):
        if delete.get(key):
            out += [f"- {key}:", "", "```", str(delete[key])[:2500], "```"]
    if delete.get("stages"):
        out.append(f"- stages: {' > '.join(s['stage'] for s in delete['stages'])}")
    elif (delete.get("progress") or {}).get("stages"):
        out.append(f"- last stages before the driver gave up: "
                   f"{' > '.join(s['stage'] for s in delete['progress']['stages'])}")
    fd = rec.get("files_after_delete") or {}
    if fd:
        out.append(f"- removed from .capella ({fd.get('removed_semantic_count')}):")
        for d in fd.get("removed_semantic", [])[:60]:
            out.append(f"  - `{d['id']}` {d.get('xsi_type') or d.get('tag')} {d.get('name') or ''}")
        out.append(f"- removed from .aird/.afm: {fd.get('removed_representation_count')} ids")
        if fd.get("new_dangling"):
            out.append(f"- NEW dangling references after delete ({len(fd['new_dangling'])}):")
            for r in fd["new_dangling"][:30]:
                out.append(f"  - {r['file']}:{r['line']} <{r['tag']} {r['attr']}> -> {r['target_id']}")
        if fd.get("diagrams_deleted"):
            out.append(f"- diagrams deleted: {_fmt([d['name'] for d in fd['diagrams_deleted']])}")
        if fd.get("diagrams_changed"):
            out.append("- diagrams changed: " + "; ".join(
                f"{d['name']} lost {len(d['lost_viewed_ids'])} views" for d in fd["diagrams_changed"]))
    if delete.get("in_memory_dangling"):
        out.append(f"- in-memory dangling right after commit ({len(delete['in_memory_dangling'])}):")
        for d in delete["in_memory_dangling"][:20]:
            out.append(f"  - {d.get('type')} `{d.get('id')}` .{d.get('feature')} -> {d.get('still_points_to')}")
    if rec.get("target_occurrences_after_delete"):
        out.append("- target uuid still in files after delete:")
        for h in rec["target_occurrences_after_delete"][:10]:
            out.append(f"  - {h['file']}:{h['line']} `{h['text'][:160]}`")
    verify = rec.get("verify") or {}
    if verify:
        out.append(f"- reopen: target_found={verify.get('target_found')}, still_present={verify.get('still_present')}")
        failed = [r for r in verify.get("refresh", []) if not r.get("ok")]
        if failed:
            out.append(f"- refresh failures: {_fmt([r['name'] + ': ' + r.get('error', '')[:200] for r in failed])}")
        if verify.get("emf_validation"):
            out.append(f"- EMF validation: `{json.dumps(verify['emf_validation'], ensure_ascii=False)[:1500]}`")
    fv = rec.get("files_after_verify") or {}
    if fv:
        out.append(f"- after reopen+refresh+save: new dangling={len(fv.get('new_dangling', []))}, "
                   f"still shown in={_fmt(fv.get('target_shown_in_diagrams_after'))}, "
                   f"diagrams deleted={_fmt([d['name'] for d in fv.get('diagrams_deleted', [])])}")
    exp = rec.get("export") or {}
    if exp:
        if exp.get("error"):
            out.append(f"- export error: {exp['error'][:500]}")
        blank = [f["name"] for f in exp.get("files", []) if f.get("blank")]
        out.append(f"- export: {len(exp.get('files', []))} PNGs, blank: {_fmt(blank)}")
    if rec.get("attach_observations"):
        out.append(f"- attach observations: {json.dumps(rec['attach_observations'], ensure_ascii=False)}")
    out.append("")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results_json", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="default: results.md next to the json")
    args = ap.parse_args(argv)
    results = json.loads(args.results_json.read_text(encoding="utf-8"))
    out = args.output or args.results_json.with_suffix(".md")
    out.write_text(render_markdown(results), encoding="utf-8")
    sys.stdout.write(f"wrote {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
