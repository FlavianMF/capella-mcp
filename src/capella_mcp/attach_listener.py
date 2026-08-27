# name : Capella MCP -- Start Attach Listener
# script-type : Python
# description : Starts the capella-mcp attach-mode listener
# onStartup : 2
# popup : enableFor(org.eclipse.core.resources.IProject)
"""EASE/python4capella script -- NOT a normal importable Python module.

This runs INSIDE the user's own Capella GUI process (not in the MCP
server's Docker/host process), using the same "Python (Py4J)" engine that
python4capella scripts already use elsewhere in this repo. See
docs/decisions/0006-attach-mode-gui-aberta.md for why this exists and
docs/architecture.md for how to register it.

Setup (one-time, per Capella GUI installation):
    Window > Preferences > Scripting > Script Locations > Add this file's
    folder, engine "Python (Py4J)" (or run scripts/register_attach_listener.py
    instead, see docs/architecture.md). The "# onStartup : 2" header above
    should then run this automatically ~2s after the GUI opens (EASE's
    generic onStartup keyword handler -- org.eclipse.ease.ui.scripts's
    StartupHandler -- confirmed to exist, but not confirmed live against
    a python4capella script specifically).

    If it doesn't start on its own, the RELIABLE manual fallback is
    EASE's own Script Explorer view, not a custom menu -- earlier
    versions of this file shipped a "# menu : ..." header expecting a
    Project Explorer right-click entry, but that keyword contributes to
    a *view's own dropdown menu* (the small chevron in a view's toolbar
    corner), not a context menu; confirmed by inspecting
    org.eclipse.ease.ui.scripts's real MenuHandler/PopupHandler bytecode
    and plugin.xml, see docs/decisions/0006-attach-mode-gui-aberta.md.
    To run manually: Window > Show View > Other... > Scripting > Script
    Explorer, find this script in the list, select it, click the toolbar's
    Run (▶) button (or right-click it there -- that's the Script
    Explorer's own native popup, always present regardless of any header
    below). After that first manual run it keeps running in the
    background for the rest of the GUI session. The "# popup" header
    above is a best-effort bonus (right-click a project elsewhere in the
    workbench) -- its exact enablement syntax against a plain Eclipse
    IProject (as opposed to a Capella model element, the only documented
    example) was not confirmed live; don't rely on it as the only path.

What it does: exposes this already-running Capella session to bridge.py
over a plain file-based protocol (no new process, no new network port --
see the "Rota B" writeup in 0006), so MCP tool calls can read/write the
SAME live session the user is looking at instead of spawning a brand new
headless Capella per call.

Protocol (~/.capella-mcp/attach/<workspace-hash>/):
    heartbeat.json      -- {pid, workspace_path, open_models, heartbeat_ts}
                            open_models is a list of absolute filesystem
                            paths, refreshed every ~3s. bridge.py treats a
                            heartbeat older than ~10s as stale (GUI closed
                            without cleanup) and falls back to spawning a
                            headless Capella, same as today.
    requests/<id>.request.json   -- {"body": "<script body text>"} written
                            by bridge.py. "body" is a plain Python(Py4J)
                            script body -- the exact same text bridge.py
                            would otherwise write into a one-shot headless
                            script (see bridge.py's _run_script/_preamble).
                            It runs here with CapellaModel/_element_id/
                            _serialize already available (mirrors
                            bridge.py's _preamble(), duplicated below since
                            this script can't import the MCP server's
                            Python package -- different process).
    requests/<id>.result.json    -- written by this listener once the
                            request's own _write_result(...) call runs;
                            bridge.py polls for this file, reads it,
                            deletes both files.

CapellaModel().open(path) reusing (not duplicating) an already-open
Sirius session for the same path, within the same JVM, was confirmed live
via a throwaway spike script (SessionManager.INSTANCE.getSessions() size
stayed at 1 across two open() calls) -- so request bodies can call
CapellaModel().open(...) exactly like the existing headless templates do,
with no special "attach" branching needed in the request body itself.
"""

include('workspace://Python4Capella/simplified_api/capella.py')

import json
import os
import time
import traceback
import hashlib

_ATTACH_ROOT = os.path.expanduser("~/.capella-mcp/attach")
_POLL_INTERVAL_SECONDS = 0.5
_HEARTBEAT_EVERY_N_POLLS = 6  # ~3s at the poll interval above


# Mirrors bridge.py::_preamble()'s helpers exactly -- keep both in sync by
# hand (this script can't import bridge.py, it runs in a different
# process/interpreter with no access to the MCP server's package).
#
# True here (unlike spawn mode's _preamble(), where it's False) so the
# SAME request-body text bridge.py already builds for every write
# dispatcher can guard its model.save() call behind `if not
# _ATTACH_MODE:` -- attach-mode writes commit into this live session but
# deliberately never force a save (docs/decisions/0006, "no forced save"
# decision): the session stays dirty exactly like an unsaved manual GUI
# edit would, until the user presses Ctrl+S themselves.
_ATTACH_MODE = True


def _element_id(el):
    try:
        return el.get_java_object().getId()
    except Exception:
        return None


def _serialize(el):
    return {
        "id": _element_id(el),
        "label": el.get_label() if hasattr(el, "get_label") else None,
        "type": type(el).__name__,
    }


def _workspace_root_os_path():
    return str(org.eclipse.core.resources.ResourcesPlugin.getWorkspace().getRoot().getLocation().toOSString())


def _workspace_key():
    return hashlib.sha1(_workspace_root_os_path().encode("utf-8")).hexdigest()[:16]


def _resolve_absolute_path(uri):
    """EMF URI -> absolute OS filesystem path, or None. Handles both
    platform:/resource/... URIs (the common case -- the model lives inside
    an Eclipse project in this workspace) and plain file: URIs."""
    try:
        if uri.isPlatformResource():
            Path = org.eclipse.core.runtime.Path
            ws_root = org.eclipse.core.resources.ResourcesPlugin.getWorkspace().getRoot()
            ifile = ws_root.getFile(Path(uri.toPlatformString(True)))
            location = ifile.getLocation()
            if location is not None:
                return location.toOSString()
        if uri.isFile():
            return uri.toFileString()
    except Exception:
        pass
    return None


def _open_model_paths():
    SessionManager = org.eclipse.sirius.business.api.session.SessionManager
    ArrayList = java.util.ArrayList
    # ArrayList(...) wrapping works around a JPMS InaccessibleObjectException
    # when Py4J reflects into SessionManager's raw (JDK-internal,
    # non-public) Collections$UnmodifiableCollection return type directly --
    # confirmed live via the same spike mentioned above.
    sessions = ArrayList(SessionManager.INSTANCE.getSessions())
    paths = []
    for i in range(sessions.size()):
        session = sessions.get(i)
        try:
            uri = session.getSessionResource().getURI()
            abs_path = _resolve_absolute_path(uri)
            if abs_path:
                paths.append(abs_path)
        except Exception:
            continue
    return paths


def _write_heartbeat(dir_path):
    payload = {
        "pid": os.getpid(),
        "workspace_path": _workspace_root_os_path(),
        "open_models": _open_model_paths(),
        "heartbeat_ts": time.time(),
    }
    tmp_path = os.path.join(dir_path, "heartbeat.json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f)
    os.replace(tmp_path, os.path.join(dir_path, "heartbeat.json"))


def _process_one_request(req_path, result_path):
    with open(req_path, "r") as f:
        payload = json.load(f)
    body = payload["body"]

    def _write_result(data):
        tmp_result = result_path + ".tmp"
        with open(tmp_result, "w") as rf:
            json.dump(data, rf)
        os.replace(tmp_result, result_path)

    local_ns = dict(globals())
    local_ns["_write_result"] = _write_result
    try:
        exec(compile(body, "<capella-mcp-attach-request>", "exec"), local_ns)
    except Exception as exc:
        _write_result({"error": str(exc), "traceback": traceback.format_exc()})


def _process_requests(requests_dir):
    for name in os.listdir(requests_dir):
        if not name.endswith(".request.json"):
            continue
        req_path = os.path.join(requests_dir, name)
        result_path = req_path[: -len(".request.json")] + ".result.json"
        try:
            _process_one_request(req_path, result_path)
        except Exception:
            traceback.print_exc()
        finally:
            try:
                os.remove(req_path)
            except OSError:
                pass


def run():
    dir_path = os.path.join(_ATTACH_ROOT, _workspace_key())
    requests_dir = os.path.join(dir_path, "requests")
    os.makedirs(requests_dir, exist_ok=True)

    _write_heartbeat(dir_path)
    polls_since_heartbeat = 0
    while True:
        try:
            _process_requests(requests_dir)
        except Exception:
            traceback.print_exc()

        polls_since_heartbeat += 1
        if polls_since_heartbeat >= _HEARTBEAT_EVERY_N_POLLS:
            try:
                _write_heartbeat(dir_path)
            except Exception:
                traceback.print_exc()
            polls_since_heartbeat = 0

        time.sleep(_POLL_INTERVAL_SECONDS)


run()
