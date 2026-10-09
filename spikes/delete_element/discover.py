"""Find candidate delete entry points in a Capella install, without
starting Capella: list the classes whose simple name matches a pattern
(default: contains "Delete") in the Capella/Sirius/EMF bundles under
``<capella>/plugins``, with each bundle's symbolic name (what
``Platform.getBundle(...)`` needs inside the probe).

The probe's candidate list (candidates.py) is a best guess from Capella's
source; this scan tells the user, on their own install, whether the
guessed class/bundle pairs exist and what else is there.

    python spikes/delete_element/discover.py /path/to/capella/plugins
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

DEFAULT_BUNDLE_PREFIXES = (
    "org.polarsys.capella.",
    "org.eclipse.sirius",
    "org.eclipse.emf.edit",
    "org.eclipse.emf.ecore",
)
DEFAULT_CLASS_PATTERN = r"Delet"


def _symbolic_name(manifest_text: str) -> str | None:
    # Manifest continuation lines start with a single space.
    unfolded = re.sub(r"\r?\n ", "", manifest_text)
    for line in unfolded.splitlines():
        if line.startswith("Bundle-SymbolicName:"):
            return line.split(":", 1)[1].split(";", 1)[0].strip()
    return None


def _class_names(entries, pattern: re.Pattern) -> list[str]:
    out = []
    for entry in entries:
        if not entry.endswith(".class") or "$" in entry:
            continue
        fqn = entry[: -len(".class")].replace("/", ".")
        simple = fqn.rsplit(".", 1)[-1]
        if pattern.search(simple):
            out.append(fqn)
    return sorted(out)


def scan_bundle(path: Path, pattern: re.Pattern) -> tuple[str | None, list[str]]:
    """(symbolic name, matching classes) for one bundle, jar or directory."""
    path = Path(path)
    if path.is_file() and path.suffix == ".jar":
        with zipfile.ZipFile(path) as jar:
            names = jar.namelist()
            manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8", "replace") if "META-INF/MANIFEST.MF" in names else ""
            classes = _class_names(names, pattern)
            # Nested library jars (Bundle-ClassPath) are common in RCP bundles.
            for inner in names:
                if inner.endswith(".jar"):
                    try:
                        with zipfile.ZipFile(io.BytesIO(jar.read(inner))) as nested:
                            classes += _class_names(nested.namelist(), pattern)
                    except zipfile.BadZipFile:
                        pass
        return _symbolic_name(manifest), sorted(set(classes))
    if path.is_dir():
        manifest_path = path / "META-INF" / "MANIFEST.MF"
        manifest = manifest_path.read_text(encoding="utf-8", errors="replace") if manifest_path.exists() else ""
        entries = [p.relative_to(path).as_posix() for p in path.rglob("*.class")]
        # dir-shaped bundles usually keep classes under bin/ or a lib jar
        entries = [e.split("/", 1)[1] if e.startswith(("bin/", "classes/")) else e for e in entries]
        classes = _class_names(entries, pattern)
        for jar_path in path.rglob("*.jar"):
            try:
                with zipfile.ZipFile(jar_path) as nested:
                    classes += _class_names(nested.namelist(), pattern)
            except zipfile.BadZipFile:
                pass
        return _symbolic_name(manifest), sorted(set(classes))
    return None, []


def discover(plugins_dir: Path, class_pattern: str = DEFAULT_CLASS_PATTERN,
             bundle_prefixes: tuple[str, ...] = DEFAULT_BUNDLE_PREFIXES) -> list[dict]:
    """[{bundle, class, source}] for every matching class in matching bundles."""
    pattern = re.compile(class_pattern)
    found = []
    for entry in sorted(Path(plugins_dir).iterdir()):
        if not entry.name.startswith(bundle_prefixes):
            continue
        bundle, classes = scan_bundle(entry, pattern)
        if bundle is None:
            continue
        for fqn in classes:
            found.append({"bundle": bundle, "class": fqn, "source": entry.name})
    return found


def index_by_simple_name(found: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for item in found:
        out.setdefault(item["class"].rsplit(".", 1)[-1], []).append(item)
    return out


_VERSION_BUNDLES = (
    "org.polarsys.capella.core.model.handler_",
    "org.polarsys.capella.core.platform.sirius.ui.commands_",
    "org.polarsys.capella.core.data.gen_",
)


def capella_version(plugins_dir: Path) -> str | None:
    """Capella version from a core bundle's file name (``<bsn>_<version>.jar``)."""
    plugins_dir = Path(plugins_dir)
    if not plugins_dir.is_dir():
        return None
    for prefix in _VERSION_BUNDLES:
        hits = sorted(p.name for p in plugins_dir.iterdir() if p.name.startswith(prefix))
        if hits:
            name = hits[-1][len(prefix):]
            return name[:-4] if name.endswith(".jar") else name
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plugins_dir", type=Path)
    ap.add_argument("--pattern", default=DEFAULT_CLASS_PATTERN)
    args = ap.parse_args(argv)
    json.dump(discover(args.plugins_dir, args.pattern), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
