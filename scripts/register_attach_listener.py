#!/usr/bin/env python3
"""One-time setup: register attach_listener.py as an EASE Script Location
in a Capella GUI workspace, without opening Capella or touching its
Preferences dialog at all.

Why this works: an EASE Script Location is just an Eclipse InstanceScope
preference -- org.eclipse.ease.ui.scripts's own PreferencesHelper.
addLocation() ultimately writes three plain lines into
<workspace>/.metadata/.plugins/org.eclipse.core.runtime/.settings/
org.eclipse.ease.ui.scripts.prefs. This isn't a guess: confirmed live by
running a throwaway headless script that called that real Java API and
reading back the resulting file (see
docs/decisions/0006-attach-mode-gui-aberta.md). This script reproduces
that same file, directly, with no Capella process involved.

Usage:
    python3 scripts/register_attach_listener.py /path/to/your/capella-workspace

Run this with Capella closed (or before ever opening that workspace) --
Eclipse reads this file once at workspace startup, so a Capella GUI
that's already running against that workspace won't pick up the change
until it's restarted.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LISTENER_DIR = (Path(__file__).resolve().parent.parent / "src" / "capella_mcp").resolve()


def _escape_value(value: str) -> str:
    # java.util.Properties value escaping -- only ':' shows up in the
    # file: URIs this script generates, so that's all this handles.
    return value.replace(":", "\\:")


def _node_name(uri: str) -> str:
    # Mirrors PreferencesHelper's own encoding, verified against a real
    # generated .prefs file (see module docstring): ':' escaped like any
    # property value, then '/' replaced with '|' -- a preference *node*
    # name can't itself contain '/', that's the node-path separator.
    return _escape_value(uri).replace("/", "|")


def register(workspace_dir: Path, listener_dir: Path = _LISTENER_DIR) -> Path:
    """Merge a Script Location entry for listener_dir into workspace_dir's
    EASE preferences. Idempotent -- safe to run again (e.g. after moving
    the checkout), and never touches any other location already
    registered there."""
    uri = f"file:{listener_dir.as_posix()}"
    node = _node_name(uri)
    settings_dir = workspace_dir / ".metadata" / ".plugins" / "org.eclipse.core.runtime" / ".settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = settings_dir / "org.eclipse.ease.ui.scripts.prefs"

    if prefs_path.exists():
        lines = prefs_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = ["eclipse.preferences.version=1"]
    lines = [line for line in lines if not line.startswith(f"{node}/")]
    lines += [
        f"{node}/default=false",
        f"{node}/location={_escape_value(uri)}",
        f"{node}/recursive=false",
    ]
    prefs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return prefs_path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} /path/to/your/capella-workspace", file=sys.stderr)
        return 2
    workspace_dir = Path(argv[1]).expanduser().resolve()
    if not workspace_dir.is_dir():
        print(f"error: {workspace_dir} is not a directory", file=sys.stderr)
        return 1

    prefs_path = register(workspace_dir)
    print(f"Registered {_LISTENER_DIR} as an EASE Script Location in:\n  {prefs_path}")
    print("Start (or restart) Capella pointing at this workspace. attach_listener.py")
    print('should autostart ("# onStartup"); if it doesn\'t, run it once manually:')
    print('right-click a project in Project Explorer > "Capella MCP -- Start Attach Listener".')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
