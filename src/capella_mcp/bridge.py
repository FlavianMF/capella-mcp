"""Headless bridge to Capella via python4capella.

Every call is a full, isolated Capella headless process: open model -> do
one operation -> (save if writing) -> exit. No state is kept between calls.
See docs/decisions/0002-headless-por-chamada.md and
docs/concepts/python4capella-api.md for the rationale and the underlying
API these scripts are generated against.

Script bodies below are only reachable through the fixed templates in this
module -- tool arguments are always interpolated via repr()/dict lookups,
never through eval() or string concatenation into executable code, so a
malicious `type_filter`/`attributes` value can at worst cause a caught
exception, not arbitrary code execution inside the Capella process.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import textwrap
import traceback
import uuid
import zipfile
from pathlib import Path

CAPELLA_BIN = os.environ.get("CAPELLA_BIN", "/opt/capella/capella")
MODELS_ROOT = Path(os.environ.get("CAPELLA_MODELS_ROOT", "/workspace/models")).resolve()
WORKSPACE_ROOT = Path(os.environ.get("CAPELLA_WORKSPACE_ROOT", "/tmp/capella-mcp-workspaces"))
DEFAULT_TIMEOUT = float(os.environ.get("CAPELLA_TIMEOUT_SECONDS", "180"))

# EASE's `workspace://` URL handler (used by include() and by the -data
# script argument itself) requires a real Eclipse *project* -- a loose file
# at the workspace root ("workspace:/script.py") fails with
# "Path must include project and resource name". So every headless call
# needs: (1) our generated script inside its own throwaway project, and
# (2) the python4capella "Python4Capella" project (which ships the
# simplified_api our preamble include()s) imported into that same fresh
# workspace -- it does NOT auto-populate a brand new -data dir on its own,
# it's normally only there because someone opened the Capella GUI once.
# Both are registered via the commandline core's `-import "path|path"`.
_SCRIPT_PROJECT_NAME = "mcp_script"

_PROJECT_DESCRIPTOR = """<?xml version="1.0" encoding="UTF-8"?>
<projectDescription>
\t<name>{name}</name>
\t<comment></comment>
\t<projects>
\t</projects>
\t<buildSpec>
\t</buildSpec>
\t<natures>
\t</natures>
</projectDescription>
"""

_python4capella_project_dir: Path | None = None


def _ensure_python4capella_project() -> Path:
    """Return a directory holding the Eclipse project for python4capella's
    simplified_api, extracting it from the installed plugin jar on first
    use and caching it under WORKSPACE_ROOT for subsequent calls."""
    global _python4capella_project_dir
    if _python4capella_project_dir is not None and (_python4capella_project_dir / ".project").exists():
        return _python4capella_project_dir

    target = WORKSPACE_ROOT / "_python4capella_project" / "Python4Capella"
    if (target / ".project").exists():
        _python4capella_project_dir = target
        return target

    capella_home = Path(CAPELLA_BIN).resolve().parent
    plugin_jars = sorted((capella_home / "plugins").glob("org.eclipse.python4capella_*.jar"))
    if not plugin_jars:
        raise BridgeError(
            f"python4capella plugin jar not found under {capella_home / 'plugins'} "
            "(expected org.eclipse.python4capella_<version>.jar)"
        )

    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(plugin_jars[-1]) as jar, jar.open("zips/Python4Capella.zip") as inner:
        with zipfile.ZipFile(io.BytesIO(inner.read())) as project_zip:
            project_zip.extractall(target)

    _python4capella_project_dir = target
    return target


# CapellaModel().open(path) resolves `path` through the same EASE
# workspace:// mechanism as include() (see CapellaPlatform.getWorkspaceFile
# in the simplified_api) -- it is NOT a raw filesystem path. So MODELS_ROOT
# itself needs to be a registered Eclipse project too, and the string
# handed to open() must be workspace-relative ("models/<model_path>"), not
# the absolute filesystem path resolve_model_path() returns.
_MODELS_PROJECT_NAME = "models"


def _ensure_models_project() -> Path:
    project_file = MODELS_ROOT / ".project"
    if not project_file.exists():
        MODELS_ROOT.mkdir(parents=True, exist_ok=True)
        project_file.write_text(
            _PROJECT_DESCRIPTOR.format(name=_MODELS_PROJECT_NAME), encoding="utf-8"
        )
    return MODELS_ROOT


def _workspace_path_for_model(abs_path: Path) -> str:
    # CapellaPlatform.getWorkspaceFile(path) builds "workspace:/" + path --
    # a single slash. EASE's workspace:// URL handler only resolves the
    # double-slash form (project name as the URL "host", e.g.
    # workspace://models/demo.aird), so we need a leading "/" here to make
    # the concatenation land on "workspace://..." instead of "workspace:/.".
    return f"/{_MODELS_PROJECT_NAME}/{abs_path.relative_to(MODELS_ROOT).as_posix()}"


LAYER_METHODS = {
    "oa": "get_operational_analysis",
    "sa": "get_system_analysis",
    "la": "get_logical_architecture",
    "pa": "get_physical_architecture",
    "epbs": "get_e_p_b_s_architecture",
}


class BridgeError(Exception):
    """Raised for anything that stops a tool call from producing a result:
    invalid model_path, Capella process failure/timeout, or an error the
    generated script itself caught and reported."""


def resolve_model_path(model_path: str) -> Path:
    """Resolve a tool-supplied model_path against MODELS_ROOT, rejecting escapes."""
    resolved = (MODELS_ROOT / model_path).resolve()
    if resolved != MODELS_ROOT and MODELS_ROOT not in resolved.parents:
        raise BridgeError(f"model_path escapes models root: {model_path!r}")
    if not resolved.exists():
        raise BridgeError(f"model not found: {model_path!r}")
    return resolved


def _preamble(result_path: Path) -> str:
    return textwrap.dedent(f"""\
        include('workspace://Python4Capella/simplified_api/capella.py')
        import json, traceback

        def _write_result(data):
            with open({str(result_path)!r}, "w") as f:
                json.dump(data, f)

        def _element_id(el):
            # NOTE: best-effort — Capella's metamodel (CapellaElement) carries a
            # persistent XMI `id`, exposed on the raw Java object. Not yet
            # validated against a running Capella instance (needs Fase 2 image +
            # a real fixture model). If this getter is wrong, get_element/
            # update_element will surface a clear BridgeError instead of
            # silently returning wrong data.
            try:
                return el.get_java_object().getId()
            except Exception:
                return None

        def _serialize(el):
            return {{
                "id": _element_id(el),
                "label": el.get_label() if hasattr(el, "get_label") else None,
                "type": type(el).__name__,
            }}
        """)


def _run_script(script_body: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    python4capella_project = _ensure_python4capella_project()
    models_project = _ensure_models_project()

    call_dir = WORKSPACE_ROOT / uuid.uuid4().hex
    project_dir = call_dir / _SCRIPT_PROJECT_NAME
    project_dir.mkdir(parents=True)
    (project_dir / ".project").write_text(
        _PROJECT_DESCRIPTOR.format(name=_SCRIPT_PROJECT_NAME), encoding="utf-8"
    )
    script_path = project_dir / "script.py"
    result_path = call_dir / "result.json"
    script_path.write_text(_preamble(result_path) + script_body, encoding="utf-8")

    cmd = [
        "xvfb-run", "-a", CAPELLA_BIN,
        "-nosplash", "-consolelog",
        "-application", "org.polarsys.capella.core.commandline.core",
        "-appid", "org.eclipse.python4capella.commandline",
        "-data", str(call_dir),
        "-import", f"{project_dir}|{python4capella_project}|{models_project}",
        f"workspace:/{project_dir.name}/{script_path.name}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"Capella headless call timed out after {timeout}s") from exc

    if not result_path.exists():
        raise BridgeError(
            "Capella headless call produced no result "
            f"(exit={proc.returncode}). stderr tail: {proc.stderr[-2000:]}"
        )

    data = json.loads(result_path.read_text(encoding="utf-8"))
    if "error" in data:
        raise BridgeError(f"{data['error']}\n{data.get('traceback', '')}")
    return data


def list_layers(model_path: str) -> dict:
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layers = []
            for key, method_name in {LAYER_METHODS!r}.items():
                layer = getattr(se, method_name)()
                layers.append({{"layer": key, "present": layer is not None}})
            _write_result({{"layers": layers}})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def list_elements(model_path: str, layer: str, type_filter: str | None = None) -> dict:
    if layer not in LAYER_METHODS:
        raise BridgeError(f"unknown layer {layer!r}, expected one of {sorted(LAYER_METHODS)}")
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    method_name = LAYER_METHODS[layer]
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layer_obj = getattr(se, {method_name!r})()
            type_filter = {type_filter!r}
            if type_filter:
                type_cls = globals().get(type_filter)
                if type_cls is None:
                    raise NameError(f"unknown Capella element type: {{type_filter}}")
                elements = layer_obj.get_all_contents_by_type(type_cls)
            else:
                elements = layer_obj.get_contents()
            _write_result({{"elements": [_serialize(el) for el in elements]}})
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def get_element(model_path: str, element_id: str) -> dict:
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            target_id = {element_id!r}
            found = None
            se = model.get_system_engineering()
            for method_name in {list(LAYER_METHODS.values())!r}:
                layer = getattr(se, method_name)()
                if layer is None:
                    continue
                for el in layer.get_all_contents() if hasattr(layer, "get_all_contents") else []:
                    if _element_id(el) == target_id:
                        found = el
                        break
                if found is not None:
                    break
            if found is None:
                _write_result({{"error": f"element not found: {{target_id}}"}})
            else:
                _write_result(_serialize(found))
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def create_element(
    model_path: str,
    layer: str,
    type_name: str,
    name: str,
    parent_id: str | None = None,
    attributes: dict | None = None,
) -> dict:
    if layer not in LAYER_METHODS:
        raise BridgeError(f"unknown layer {layer!r}, expected one of {sorted(LAYER_METHODS)}")
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    method_name = LAYER_METHODS[layer]
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            se = model.get_system_engineering()
            layer_obj = getattr(se, {method_name!r})()

            cls = globals().get({type_name!r})
            if cls is None:
                raise NameError(f"unknown Capella element type: {type_name!r}")

            model.start_transaction()
            try:
                el = cls()
                # NOTE: best-effort attribute wiring, unverified against a real
                # model (needs Fase 2 image + fixture). `name` covers the one
                # attribute virtually every Capella element (NamedElement) has;
                # `attributes` beyond that use a set_<key> convention mirrored
                # from the observed get_<key> convention in the simplified API.
                if hasattr(el, "set_name"):
                    el.set_name({name!r})
                for key, value in ({attributes!r} or {{}}).items():
                    setter = getattr(el, f"set_{{key}}", None)
                    if setter is not None:
                        setter(value)
                parent_id = {parent_id!r}
                if parent_id is not None:
                    container = None
                    for candidate in layer_obj.get_all_contents() if hasattr(layer_obj, "get_all_contents") else []:
                        if _element_id(candidate) == parent_id:
                            container = candidate
                            break
                    if container is None:
                        raise ValueError(f"parent_id not found in layer: {{parent_id}}")
                else:
                    container = layer_obj

                # NOTE: get_contents() returns a disconnected Python-side
                # snapshot of eContents() (see capella.py), not a live EMF
                # collection -- appending to it never actually attaches the
                # element to the model tree. The real containment
                # collections are per-type ("owned_<...>", a live EList
                # supporting .add()), e.g. a LogicalComponent lives under
                # LogicalComponentPkg.get_owned_logical_components() (root)
                # or another LogicalComponent's own
                # get_owned_logical_components() (nesting) -- only this one
                # combination has been validated against a real model.
                # Other layer/type combinations still need their own
                # container resolved the same way before create_element
                # will actually persist anything for them.
                if {type_name!r} == "LogicalComponent":
                    if hasattr(container, "get_logical_component_pkg"):
                        container = container.get_logical_component_pkg()
                    if not hasattr(container, "get_owned_logical_components"):
                        raise AttributeError(
                            "no get_owned_logical_components() container found "
                            f"on {{type(container).__name__}}"
                        )
                    container.get_owned_logical_components().add(el)
                else:
                    container.get_contents().append(el)
                model.commit_transaction()
            except Exception:
                model.rollback_transaction()
                raise
            model.save()
            _write_result(_serialize(el))
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)


def update_element(model_path: str, element_id: str, attributes: dict) -> dict:
    abs_path = resolve_model_path(model_path)
    workspace_path = _workspace_path_for_model(abs_path)
    body = textwrap.dedent(f"""\
        try:
            model = CapellaModel()
            model.open({workspace_path!r})
            target_id = {element_id!r}
            se = model.get_system_engineering()
            found = None
            for method_name in {list(LAYER_METHODS.values())!r}:
                layer = getattr(se, method_name)()
                if layer is None:
                    continue
                for el in layer.get_all_contents() if hasattr(layer, "get_all_contents") else []:
                    if _element_id(el) == target_id:
                        found = el
                        break
                if found is not None:
                    break
            if found is None:
                _write_result({{"error": f"element not found: {{target_id}}"}})
            else:
                model.start_transaction()
                try:
                    for key, value in ({attributes!r} or {{}}).items():
                        setter = getattr(found, f"set_{{key}}", None)
                        if setter is not None:
                            setter(value)
                    model.commit_transaction()
                except Exception:
                    model.rollback_transaction()
                    raise
                model.save()
                _write_result(_serialize(found))
        except Exception as exc:
            _write_result({{"error": str(exc), "traceback": traceback.format_exc()}})
        """)
    return _run_script(body)
