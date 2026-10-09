"""Pure, Capella-free analysis of a Capella model directory on disk.

Used by the delete_element phase 1 spike (see
docs/spikes/delete-element-spike.md) to answer "what disappeared from the
.capella/.aird files, and what still points at it" without trusting the
Capella process that did the delete. Everything here works on the saved
XMI text only, so it is unit-testable without Capella
(spikes/delete_element/tests/).

Model file conventions it relies on (checked against
tests/fixtures/car_hmi, Capella 7.0.1):

- .capella: every model element carries ``id="<uuid>"``; intra-file
  references are attribute values of the form ``#<uuid>`` (several
  space-separated for many-valued features, e.g. ``involved="#a #b"``).
- .aird: Sirius objects carry ``uid="_..."``, GMF notation objects
  ``xmi:id="_..."``; references to semantic elements are
  ``href="<model>.capella#<uuid>"`` on child elements (``<target>``,
  ``<semanticElements>``); intra-file references are bare ``_...`` ids or
  ``#_...`` (``element=``, ``repPath=``, ``selectedViews=``, ...).
- .afm: ``id="_..."``.

Only references whose target file is part of the scanned model are
checked; ``platform:/plugin/...`` hrefs (odesign, metamodel) are ignored.
"""

from __future__ import annotations

import re
import xml.parsers.expat
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODEL_SUFFIXES = (".capella", ".capellafragment", ".aird", ".airdfragment", ".afm")

# Capella semantic ids are UUIDs; Sirius/GMF/EMF generated ids are "_" + 22
# url-safe base64 chars (EcoreUtil.generateUUID()).
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
EMF_ID_RE = re.compile(r"^_[A-Za-z0-9_-]{22}$")

DEF_ATTRS = ("id", "uid", "xmi:id")
# Free-text attributes: never parsed for id tokens, even if a word in them
# happens to look like "#<uuid>".
TEXT_ATTRS = frozenset({
    "name", "summary", "description", "documentation", "label", "value",
    "body", "expression", "key", "comment", "fontName", "vpId", "version",
    "xmi:version", "xsi:schemaLocation", "changeId",
})


def _looks_like_id(token: str) -> bool:
    return bool(UUID_RE.match(token) or EMF_ID_RE.match(token))


@dataclass(frozen=True)
class Definition:
    id: str
    file: str
    line: int
    tag: str
    xsi_type: str | None
    name: str | None


@dataclass(frozen=True)
class Reference:
    file: str
    line: int
    tag: str
    attr: str
    target_file: str
    target_id: str
    owner_id: str | None  # nearest enclosing element with an id/uid


@dataclass
class DiagramInfo:
    uid: str                       # DSemanticDiagram uid (the representation)
    descriptor_uid: str | None = None
    name: str | None = None
    target_id: str | None = None   # semantic root of the diagram
    viewed_ids: set[str] = field(default_factory=set)  # semantic ids shown by its diagram elements


@dataclass
class ModelScan:
    root: str
    files: list[str]
    defs: dict[tuple[str, str], Definition]
    refs: list[Reference]
    diagrams: dict[str, DiagramInfo]

    def ids_in(self, file: str) -> set[str]:
        return {i for (f, i) in self.defs if f == file}

    def semantic_defs(self) -> dict[str, Definition]:
        return {d.id: d for d in self.defs.values() if d.file.endswith((".capella", ".capellafragment"))}


def model_files(root: Path) -> list[Path]:
    root = Path(root)
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in MODEL_SUFFIXES)


def _split_href(href: str, current_file: str, known_files: set[str]) -> tuple[str, str] | None:
    if "#" not in href:
        return None
    file_part, frag = href.split("#", 1)
    if file_part == "":
        target_file = current_file
    else:
        if ":" in file_part.split("/")[0]:  # platform:/..., http://...
            return None
        target_file = Path(file_part).name
        if target_file not in known_files:
            return None
    if not _looks_like_id(frag):
        return None
    return target_file, frag


def scan_file(path: Path, known_files: set[str]) -> tuple[list[Definition], list[Reference], dict[str, DiagramInfo]]:
    """Parse one XMI file. ``known_files`` is the set of basenames of every
    model file being scanned, used to decide which hrefs are local."""
    fname = Path(path).name
    defs: list[Definition] = []
    refs: list[Reference] = []
    diagrams: dict[str, DiagramInfo] = {}
    descriptors: list[tuple[str, str | None, str]] = []  # (descriptor uid, name, repPath id)

    # stack entries: (tag, own_id, xsi_type)
    stack: list[tuple[str, str | None, str | None]] = []
    parser = xml.parsers.expat.ParserCreate()

    def owner_id() -> str | None:
        for _tag, oid, _t in reversed(stack):
            if oid is not None:
                return oid
        return None

    def current_diagram() -> str | None:
        for tag, oid, _t in stack:
            if tag == "diagram:DSemanticDiagram" and oid is not None:
                return oid
        return None

    def start(tag: str, attrs: dict[str, str]) -> None:
        line = parser.CurrentLineNumber
        xsi_type = attrs.get("xsi:type") or attrs.get("xmi:type")
        own_id = None
        for a in DEF_ATTRS:
            if a in attrs and _looks_like_id(attrs[a]):
                own_id = attrs[a]
                defs.append(Definition(own_id, fname, line, tag, xsi_type, attrs.get("name")))
                break
        parent_owner = owner_id()

        if tag == "diagram:DSemanticDiagram" and own_id is not None:
            diagrams.setdefault(own_id, DiagramInfo(uid=own_id))
        if tag == "ownedRepresentationDescriptors" and own_id is not None:
            rep = attrs.get("repPath", "").lstrip("#")
            descriptors.append((own_id, attrs.get("name"), rep))

        for attr, value in attrs.items():
            if attr in DEF_ATTRS or attr in TEXT_ATTRS or attr in ("xsi:type", "xmi:type"):
                continue
            if attr == "href":
                split = _split_href(value, fname, known_files)
                if split is not None:
                    tfile, tid = split
                    refs.append(Reference(fname, line, tag, attr, tfile, tid, parent_owner))
                    diag = current_diagram()
                    if diag is not None and tfile.endswith((".capella", ".capellafragment")):
                        # direct child <target> of the DSemanticDiagram = the diagram root;
                        # anything deeper = a diagram element's semantic target.
                        if tag == "target" and stack and stack[-1][0] == "diagram:DSemanticDiagram":
                            diagrams[diag].target_id = tid
                        elif tag in ("target", "semanticElements"):
                            diagrams[diag].viewed_ids.add(tid)
                continue
            for token in value.split():
                tid = token[1:] if token.startswith("#") else token
                if not _looks_like_id(tid):
                    continue
                # A bare token is only a reference in XMI files that use bare
                # ids (.aird); .capella intra-file refs always carry "#".
                if not token.startswith("#") and not EMF_ID_RE.match(tid):
                    continue
                refs.append(Reference(fname, line, tag, attr, fname, tid, own_id or parent_owner))

        stack.append((tag, own_id, xsi_type))

    def end(_tag: str) -> None:
        stack.pop()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    with open(path, "rb") as f:
        parser.ParseFile(f)

    for desc_uid, name, rep in descriptors:
        info = diagrams.setdefault(rep, DiagramInfo(uid=rep))
        info.descriptor_uid = desc_uid
        info.name = name
    return defs, refs, diagrams


def scan_model(root: Path) -> ModelScan:
    """Scan every model file under ``root`` (a model directory)."""
    root = Path(root)
    files = model_files(root)
    known = {p.name for p in files}
    all_defs: dict[tuple[str, str], Definition] = {}
    all_refs: list[Reference] = []
    all_diagrams: dict[str, DiagramInfo] = {}
    for p in files:
        defs, refs, diagrams = scan_file(p, known)
        for d in defs:
            all_defs[(d.file, d.id)] = d
        all_refs.extend(refs)
        all_diagrams.update(diagrams)
    return ModelScan(str(root), [p.name for p in files], all_defs, all_refs, all_diagrams)


def dangling_refs(scan: ModelScan) -> list[Reference]:
    """References whose target id is not defined in the target file."""
    return [r for r in scan.refs if (r.target_file, r.target_id) not in scan.defs]


def find_occurrences(root: Path, needle: str) -> list[dict]:
    """Every line of every model file under ``root`` containing ``needle``."""
    hits = []
    for p in model_files(Path(root)):
        with open(p, encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if needle in line:
                    hits.append({"file": p.name, "line": lineno, "text": line.strip()[:240]})
    return hits


def _def_dict(d: Definition) -> dict:
    return asdict(d)


def _ref_key(r: Reference) -> tuple:
    return (r.file, r.tag, r.attr, r.target_file, r.target_id, r.owner_id)


def compare_scans(before: ModelScan, after: ModelScan, target_id: str | None = None) -> dict:
    """JSON-ready diff between two scans of the same model.

    - ``removed`` / ``added``: definitions present on one side only, split
      into semantic (.capella) and representation (.aird/.afm) ids.
    - ``new_dangling``: references dangling after that were not dangling
      before (pre-existing breakage in the fixture is not blamed on the
      delete).
    - ``diagrams_deleted``: representations gone; ``diagrams_changed``:
      representations still there whose set of shown semantic ids shrank,
      with the ids that left.
    - ``target_*``: whether the target id is still defined / referenced.
    """
    removed_keys = set(before.defs) - set(after.defs)
    added_keys = set(after.defs) - set(before.defs)

    def split(keys, scan):
        sem, rep = [], []
        for k in sorted(keys):
            d = scan.defs[k]
            (sem if d.file.endswith((".capella", ".capellafragment")) else rep).append(_def_dict(d))
        return sem, rep

    removed_sem, removed_rep = split(removed_keys, before)
    added_sem, added_rep = split(added_keys, after)

    dangling_before = {_ref_key(r) for r in dangling_refs(before)}
    dangling_after = dangling_refs(after)
    new_dangling = [asdict(r) for r in dangling_after if _ref_key(r) not in dangling_before]

    diagrams_deleted = []
    diagrams_changed = []
    for uid, info in before.diagrams.items():
        if uid not in after.diagrams:
            diagrams_deleted.append({"uid": info.descriptor_uid or uid, "name": info.name, "target_id": info.target_id})
            continue
        lost = sorted(info.viewed_ids - after.diagrams[uid].viewed_ids)
        if lost:
            diagrams_changed.append({
                "uid": info.descriptor_uid or uid, "name": info.name, "lost_viewed_ids": lost,
            })

    result = {
        "removed_semantic": removed_sem,
        "removed_semantic_count": len(removed_sem),
        "removed_representation_count": len(removed_rep),
        "added_semantic": added_sem,
        "added_representation_count": len(added_rep),
        "dangling_before_count": len(dangling_before),
        "dangling_after_count": len(dangling_after),
        "new_dangling": new_dangling,
        "diagrams_deleted": diagrams_deleted,
        "diagrams_changed": diagrams_changed,
    }
    if target_id is not None:
        result["target_defined_after"] = any(i == target_id for (_f, i) in after.defs)
        result["target_referenced_after"] = [
            asdict(r) for r in after.refs if r.target_id == target_id
        ]
        result["target_shown_in_diagrams_after"] = sorted(
            (info.name or uid) for uid, info in after.diagrams.items() if target_id in info.viewed_ids
        )
    return result


def views_of(scan: ModelScan, semantic_id: str) -> list[dict]:
    """Diagrams that show ``semantic_id`` (as a diagram element) or are rooted at it."""
    out = []
    for uid, info in scan.diagrams.items():
        rooted = info.target_id == semantic_id
        shown = semantic_id in info.viewed_ids
        if rooted or shown:
            out.append({"uid": info.descriptor_uid or uid, "name": info.name, "rooted": rooted, "shown": shown})
    return out
