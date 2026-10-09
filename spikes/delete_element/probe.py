"""Capella-side script bodies for the delete_element phase 1 spike.

Each ``*_body(params)`` returns a python4capella (EASE Py4J) script body
for ``capella_mcp.bridge._run_script`` (spawn) or
``bridge._run_script_attach`` (attach): the bridge's own preamble
(``_write_result``, ``_ATTACH_MODE``, ``CapellaModel``) is prepended by
the bridge / attach listener, exactly like a production tool.

Spike-only code: the bodies are fixed text, parameters reach them only as
one ``_PARAMS = <repr(dict)>`` line (same rule as bridge.py: no generated
code). They are deliberately defensive: every Java call that might not
exist on this Capella version is wrapped and its exception recorded, so
one run returns enough diagnostics to fix the candidate list instead of a
bare stack trace. Nothing here is meant to be merged into bridge.py as-is.

The bodies never write a top-level ``"error"`` key (that would make
``_run_script`` raise and lose the diagnostics); failures go in
``probe_error`` / per-step ``error`` fields.
"""

from __future__ import annotations

_COMMON = r'''
import json as _json
import time as _time
import traceback as _traceback

_P = _PARAMS
_T0 = _time.time()
_REPORT = {"stages": []}


def _stage(name, **extra):
    entry = {"stage": name, "t": round(_time.time() - _T0, 2)}
    entry.update(extra)
    _REPORT["stages"].append(entry)
    path = _P.get("progress_path")
    if path:
        try:
            with open(path, "w") as f:
                _json.dump(_REPORT, f, default=str)
        except Exception:
            pass


def _err(exc, limit=6000):
    text = str(exc)
    return text[:limit]


_Platform = org.eclipse.core.runtime.Platform


def _load(bundle_name, fqn):
    bundle = _Platform.getBundle(bundle_name)
    if bundle is None:
        raise RuntimeError("bundle not found: " + bundle_name)
    return bundle.loadClass(fqn)


_OBJECT = _load("org.eclipse.core.runtime", "java.lang.Object")
_COLLECTION = _load("org.eclipse.core.runtime", "java.util.Collection")


def _obj_array(items):
    arr = java.lang.reflect.Array.newInstance(_OBJECT, len(items))
    for i, item in enumerate(items):
        java.lang.reflect.Array.set(arr, i, item)
    return arr


def _jlist(coll):
    if coll is None:
        return []
    al = java.util.ArrayList(coll)
    return [al.get(i) for i in range(al.size())]


def _oid(o):
    if o is None:
        return None
    for getter in ("getId", "getUid"):
        try:
            v = getattr(o, getter)()
            if v:
                return str(v)
        except Exception:
            pass
    return None


def _odesc(o):
    d = {"id": _oid(o), "type": None, "label": None, "resource": None}
    try:
        d["type"] = str(o.eClass().getName())
    except Exception:
        pass
    try:
        name = o.getName()
        d["label"] = str(name) if name is not None else None
    except Exception:
        pass
    try:
        r = o.eResource()
        d["resource"] = str(r.getURI().lastSegment()) if r is not None else None
    except Exception:
        pass
    return d


def _semantic_objects(model):
    out = []
    for res in _jlist(model.session.getSemanticResources()):
        it = res.getAllContents()
        while it.hasNext():
            out.append(it.next())
    return out


def _semantic_index(model):
    idx = {}
    for o in _semantic_objects(model):
        i = _oid(o)
        if i:
            idx[i] = o
    return idx


def _accepts(ptype, arg):
    name = str(ptype.getName())
    if isinstance(arg, bool):
        return name in ("boolean", "java.lang.Boolean")
    if isinstance(arg, str):
        return name in ("java.lang.String", "java.lang.Object", "java.lang.CharSequence")
    if arg is None:
        return not ptype.isPrimitive()
    try:
        return bool(ptype.isInstance(arg))
    except Exception:
        return False


def _invoke_static(cls, name, args):
    tried = []
    for m in cls.getMethods():
        if str(m.getName()) != name or m.getParameterCount() != len(args):
            continue
        ptypes = m.getParameterTypes()
        tried.append(str(m.toGenericString()))
        if all(_accepts(ptypes[i], args[i]) for i in range(len(args))):
            return m.invoke(None, _obj_array(args))
    raise RuntimeError("no matching static %s.%s for %d args; candidates: %s"
                       % (cls.getName(), name, len(args), tried))


def _diagrams(model, watch_ids):
    """Every representation descriptor with its root, the semantic ids its
    elements show, and how many of its elements point at nothing."""
    DM = org.eclipse.sirius.business.api.dialect.DialectManager.INSTANCE
    watch = set(watch_ids or [])
    out = []
    for d in _jlist(DM.getAllRepresentationDescriptors(model.session)):
        info = {"uid": str(d.getUid()), "name": str(d.getName()), "target_id": None,
                "element_count": 0, "dangling_views": 0, "watched_views": []}
        try:
            info["target_id"] = _oid(d.getTarget())
        except Exception:
            pass
        try:
            rep = d.getRepresentation()
            for e in _jlist(rep.getRepresentationElements()):
                info["element_count"] += 1
                t = e.getTarget()
                if t is None or t.eIsProxy() or t.eResource() is None:
                    info["dangling_views"] += 1
                else:
                    tid = _oid(t)
                    if tid in watch:
                        info["watched_views"].append(tid)
        except Exception as exc:
            info["error"] = _err(exc, 500)
        out.append(info)
    return out


def _env():
    env = {"attach_mode": bool(_ATTACH_MODE)}
    try:
        env["thread"] = str(java.lang.Thread.currentThread().getName())
    except Exception as exc:
        env["thread_error"] = _err(exc, 300)
    try:
        display_cls = _load("org.eclipse.swt", "org.eclipse.swt.widgets.Display")
        env["on_ui_thread"] = display_cls.getMethod("getCurrent", None).invoke(None, None) is not None
    except Exception as exc:
        env["on_ui_thread_error"] = _err(exc, 300)
    try:
        pui = _load("org.eclipse.ui.workbench", "org.eclipse.ui.PlatformUI")
        env["workbench_running"] = bool(pui.getMethod("isWorkbenchRunning", None).invoke(None, None))
    except Exception as exc:
        env["workbench_running_error"] = _err(exc, 300)
    return env
'''

_DELETE = r'''
_MISSING = object()


def _execution_manager(model, target):
    errors = []
    for bundle_name, cls_name, method in _P["em_lookups"]:
        try:
            cls = _load(bundle_name, cls_name)
            if method == "getInstance":
                reg = _invoke_static(cls, "getInstance", [])
                em = reg.getExecutionManager(model.session.getTransactionalEditingDomain())
                how = cls_name + ".getInstance().getExecutionManager(domain)"
            else:
                em = _invoke_static(cls, method, [target])
                how = cls_name + "." + method + "(element)"
            if em is not None:
                return em, how
            errors.append(how + " returned null")
        except Exception as exc:
            errors.append(cls_name + ": " + _err(exc, 600))
    raise RuntimeError("no ExecutionManager found: " + " | ".join(errors))


def _bool_fields(obj):
    out = {}
    cls = obj.getClass()
    while cls is not None and str(cls.getName()) != "java.lang.Object":
        for f in cls.getDeclaredFields():
            try:
                if str(f.getType().getName()) != "boolean":
                    continue
                if java.lang.reflect.Modifier.isStatic(f.getModifiers()):
                    continue
                f.setAccessible(True)
                out[str(cls.getSimpleName()) + "." + str(f.getName())] = bool(f.getBoolean(obj))
            except Exception:
                out[str(cls.getSimpleName()) + "." + str(f.getName())] = None
        cls = cls.getSuperclass()
    return out


def _set_bool_fields(obj, substring, value):
    changed = []
    cls = obj.getClass()
    while cls is not None and str(cls.getName()) != "java.lang.Object":
        for f in cls.getDeclaredFields():
            try:
                if str(f.getType().getName()) != "boolean" or substring not in str(f.getName()).lower():
                    continue
                if java.lang.reflect.Modifier.isStatic(f.getModifiers()):
                    continue
                f.setAccessible(True)
                f.setBoolean(obj, value)
                changed.append(str(f.getName()))
            except Exception:
                pass
        cls = cls.getSuperclass()
    return changed


def _construct(cls, pool, bool_rules, info):
    """Pick the public constructor whose non-boolean parameters can all be
    filled from ``pool``, preferring the most non-boolean then the most
    boolean parameters; map each boolean slot to the field it sets by
    constructing throwaway instances; then build the real one with
    ``bool_rules`` applied by field name."""
    ctors = list(cls.getConstructors())
    info["constructors"] = [str(c.toGenericString()) for c in ctors]
    best = None
    for c in ctors:
        args, bool_slots, ok = [], [], True
        for i, pt in enumerate(c.getParameterTypes()):
            if str(pt.getName()) == "boolean":
                args.append(False)
                bool_slots.append(i)
                continue
            match = _MISSING
            for v in pool:
                if v is not None and _accepts(pt, v):
                    match = v
                    break
            if match is _MISSING:
                ok = False
                break
            args.append(match)
        if not ok:
            continue
        score = (len(args) - len(bool_slots), len(bool_slots))
        if best is None or score > best[0]:
            best = (score, c, args, bool_slots)
    if best is None:
        raise RuntimeError("no constructor fillable from the pool")
    _score, ctor, args, bool_slots = best
    info["constructor"] = str(ctor.toGenericString())

    base = _bool_fields(ctor.newInstance(_obj_array(args)))
    mapping = {}
    for slot in bool_slots:
        trial = list(args)
        trial[slot] = True
        try:
            fields = _bool_fields(ctor.newInstance(_obj_array(trial)))
            mapping[slot] = sorted(k for k, v in fields.items() if v != base.get(k))
        except Exception as exc:
            mapping[slot] = ["<ctor failed: " + _err(exc, 300) + ">"]
    info["bool_slot_fields"] = {str(k): v for k, v in mapping.items()}

    final = list(args)
    decided = {}
    for slot in bool_slots:
        names = " ".join(mapping.get(slot, [])).lower()
        value = False
        for sub, v in bool_rules.items():
            if sub.lower() in names:
                value = bool(v)
                break
        final[slot] = value
        decided[str(slot)] = value
    info["bool_values"] = decided
    obj = ctor.newInstance(_obj_array(final))
    info["forced_confirm_false"] = _set_bool_fields(obj, "confirm", False)
    info["bool_fields_final"] = _bool_fields(obj)
    return obj


def _deleted_collections(cmd):
    """Zero-arg getters whose name mentions delete and return a Collection
    (e.g. a computed 'all elements to delete' set): record their content."""
    out = {}
    try:
        for m in cmd.getClass().getMethods():
            name = str(m.getName())
            if m.getParameterCount() != 0 or not name.startswith("get") or "elet" not in name.lower():
                continue
            try:
                v = m.invoke(cmd, None)
                if v is not None and _COLLECTION.isInstance(v):
                    items = _jlist(v)
                    out[name] = {"size": len(items), "items": [_odesc(x) for x in items[:200]]}
            except Exception as exc:
                out[name] = {"error": _err(exc, 300)}
    except Exception as exc:
        out["_error"] = _err(exc, 300)
    return out


def _run_mechanism(model, mech, target, info):
    kind = mech["kind"]
    domain = model.session.getTransactionalEditingDomain()
    selection = java.util.ArrayList()
    selection.add(target)
    cls = _load(mech["bundle"], mech["class"])
    info["class_loaded"] = str(cls.getName())

    em = None
    if kind == "capella_command":
        em, how = _execution_manager(model, target)
        info["execution_manager"] = how
    pool = [em, domain, selection, model.session, target]

    if kind == "capella_command":
        cmd = _construct(cls, pool, mech.get("bool_rules", {}), info)
    elif kind == "emf_command":
        if mech.get("factory"):
            cmd = _invoke_static(cls, mech["factory"], [domain, selection])
            info["constructor"] = mech["class"] + "." + mech["factory"] + "(domain, [element])"
        else:
            cmd = _construct(cls, pool, mech.get("bool_rules", {}), info)
    else:
        cmd = None
    info["mechanism_ready"] = True
    _stage("mechanism_ready")

    def _execute():
        if kind == "capella_command":
            if mech.get("invoke") == "em_execute":
                em.execute(cmd)
            else:
                cmd.run()
        elif kind == "emf_command":
            can = cmd.canExecute()
            info["can_execute"] = bool(can)
            if not can:
                raise RuntimeError("command.canExecute() returned false")
            cmd.execute()
        elif kind == "static_call":
            _invoke_static(cls, mech["method"], [target, model.session])
        elif kind == "ecoreutil":
            _invoke_static(cls, "delete", [target, True])
        else:
            raise RuntimeError("unknown mechanism kind: " + kind)

    _stage("executing")
    if mech.get("in_transaction"):
        model.start_transaction()
        try:
            _execute()
            _stage("executed_in_transaction")
            model.commit_transaction()
            info["committed"] = True
        except Exception:
            try:
                model.rollback_transaction()
                info["rolled_back"] = True
            except Exception as rb_exc:
                info["rollback_error"] = _err(rb_exc, 1000)
            raise
    else:
        _execute()
        info["committed"] = True
    _stage("executed")
    if cmd is not None:
        info["command_collections"] = _deleted_collections(cmd)


def _probe():
    out = {"probe": "delete", "mechanism": _P["mechanism_name"], "case": _P["case"],
           "element_id": _P["element_id"], "env": _env()}
    model = CapellaModel()
    model.open(_P["model_workspace_path"])
    _stage("opened")

    idx_before = _semantic_index(model)
    target = idx_before.get(_P["element_id"])
    if target is None:
        out["probe_error"] = "element not found: " + _P["element_id"]
        return out
    out["element"] = _odesc(target)

    contained = [target]
    it = target.eAllContents()
    while it.hasNext():
        contained.append(it.next())
    out["contained_before"] = [_odesc(o) for o in contained]

    inverse = []
    try:
        xref = model.session.getSemanticCrossReferencer()
        for o in contained:
            for s in _jlist(xref.getInverseReferences(o)):
                owner = s.getEObject()
                feat = s.getEStructuralFeature()
                d = _odesc(owner)
                d["feature"] = str(feat.getName())
                d["points_to"] = _oid(o)
                inverse.append((owner, str(feat.getName()), d))
    except Exception as exc:
        out["inverse_error"] = _err(exc, 1000)
    out["inverse_before"] = [d for _o, _f, d in inverse]

    watch = [d["id"] for d in out["contained_before"] if d["id"]]
    out["diagrams_before"] = _diagrams(model, watch)
    _stage("snapshot_before")

    info = {}
    out["mechanism_info"] = info
    t_exec = _time.time()
    try:
        _run_mechanism(model, _P["mechanism"], target, info)
        out["delete_ok"] = True
    except Exception as exc:
        out["delete_ok"] = False
        out["delete_exception"] = _err(exc)
        out["delete_traceback"] = _traceback.format_exc()[-6000:]
        if not info.get("mechanism_ready"):
            out["mechanism_unavailable"] = True
    out["delete_seconds"] = round(_time.time() - t_exec, 2)
    out["env_after"] = _env()

    idx_after = _semantic_index(model)
    removed = [i for i in idx_before if i not in idx_after]
    out["removed_semantic"] = [_odesc(idx_before[i]) for i in removed]
    out["removed_semantic_count"] = len(removed)
    out["target_detached"] = target.eResource() is None

    dangling = []
    for owner, feat_name, d in inverse:
        try:
            if owner.eResource() is None:
                continue
            f = owner.eClass().getEStructuralFeature(feat_name)
            v = owner.eGet(f)
            values = _jlist(v) if _COLLECTION.isInstance(v) else [v]
            for x in values:
                if x is not None and (x.eIsProxy() or x.eResource() is None):
                    dd = dict(d)
                    dd["still_points_to"] = _oid(x)
                    dangling.append(dd)
        except Exception as exc:
            dd = dict(d)
            dd["check_error"] = _err(exc, 300)
            dangling.append(dd)
    out["in_memory_dangling"] = dangling
    out["diagrams_after"] = _diagrams(model, watch)
    before_uids = {d["uid"] for d in out["diagrams_before"]}
    after_uids = {d["uid"] for d in out["diagrams_after"]}
    out["diagrams_deleted"] = [d for d in out["diagrams_before"] if d["uid"] not in after_uids]
    out["diagrams_added"] = [d for d in out["diagrams_after"] if d["uid"] not in before_uids]
    _stage("snapshot_after")

    out["saved"] = False
    if out["delete_ok"] and not _ATTACH_MODE:
        try:
            model.save()
            out["saved"] = True
        except Exception as exc:
            out["save_error"] = _err(exc)
    _stage("saved" if out["saved"] else "not_saved")
    return out


try:
    _result = _probe()
except Exception as _exc:
    _result = {"probe": "delete", "probe_error": _err(_exc), "traceback": _traceback.format_exc()[-6000:]}
_result["stages"] = _REPORT["stages"]
_write_result(_result)
'''

_VERIFY = r'''
def _probe():
    out = {"probe": "verify", "env": _env()}
    model = CapellaModel()
    model.open(_P["model_workspace_path"])
    _stage("opened")
    idx = _semantic_index(model)
    out["target_found"] = _P["element_id"] in idx
    out["still_present"] = [i for i in _P.get("removed_ids", []) if i in idx]
    watch = [_P["element_id"]] + list(_P.get("removed_ids", []))
    out["diagrams_before_refresh"] = _diagrams(model, watch)

    if _P.get("validate", True):
        try:
            roots = []
            for res in _jlist(model.session.getSemanticResources()):
                roots += _jlist(res.getContents())
            diag_cls = _load("org.eclipse.emf.ecore", "org.eclipse.emf.ecore.util.Diagnostician")
            diagnostician = diag_cls.getField("INSTANCE").get(None)
            messages = []
            worst = 0
            for r in roots:
                dg = diagnostician.validate(r)
                worst = max(worst, dg.getSeverity())
                for child in _jlist(dg.getChildren()):
                    if child.getSeverity() >= 4:  # ERROR
                        messages.append(str(child.getMessage())[:400])
            out["emf_validation"] = {"worst_severity": worst, "errors": messages[:50], "error_count": len(messages)}
        except Exception as exc:
            out["emf_validation"] = {"error": _err(exc, 1000)}
    _stage("validated")

    refresh = []
    if _P.get("refresh", True):
        DM = org.eclipse.sirius.business.api.dialect.DialectManager.INSTANCE
        monitor = org.eclipse.core.runtime.NullProgressMonitor()
        model.start_transaction()
        try:
            for d in _jlist(DM.getAllRepresentationDescriptors(model.session)):
                entry = {"uid": str(d.getUid()), "name": str(d.getName())}
                try:
                    DM.refresh(d.getRepresentation(), monitor)
                    entry["ok"] = True
                except Exception as exc:
                    entry["ok"] = False
                    entry["error"] = _err(exc, 600)
                refresh.append(entry)
            model.commit_transaction()
        except Exception as exc:
            model.rollback_transaction()
            out["refresh_error"] = _err(exc)
        out["refresh"] = refresh
        out["diagrams_after_refresh"] = _diagrams(model, watch)
        if not _ATTACH_MODE:
            try:
                model.save()
                out["saved"] = True
            except Exception as exc:
                out["save_error"] = _err(exc)
    _stage("done")
    return out


try:
    _result = _probe()
except Exception as _exc:
    _result = {"probe": "verify", "probe_error": _err(_exc), "traceback": _traceback.format_exc()[-6000:]}
_result["stages"] = _REPORT["stages"]
_write_result(_result)
'''

_SEED = r'''
def _probe():
    out = {"probe": "seed", "steps": []}
    model = CapellaModel()
    model.open(_P["model_workspace_path"])
    idx = _semantic_index(model)
    ids = _P["ids"]
    cap = idx[ids["capability"]]
    motorista = idx[ids["actor"]]
    veiculo = idx[ids["entity_with_child"]]
    monitorar = idx[ids["oa_leaf_in_oabd"]]
    version = capella_version()

    def step(name, fn):
        model.start_transaction()
        try:
            fn()
            model.commit_transaction()
            out["steps"].append({"step": name, "ok": True})
        except Exception as exc:
            model.rollback_transaction()
            out["steps"].append({"step": name, "ok": False, "error": _err(exc, 2000)})

    def entity_involvements():
        cls = get_e_classifier("http://www.polarsys.org/capella/core/oa/" + version, "EntityOperationalCapabilityInvolvement")
        for ent in (motorista, veiculo):
            inv = create_e_object_from_e_classifier(cls)
            inv.setInvolved(ent)
            cap.getOwnedEntityOperationalCapabilityInvolvements().add(inv)

    def activity_involvement():
        cls = get_e_classifier("http://www.polarsys.org/capella/core/interaction/" + version, "AbstractFunctionAbstractCapabilityInvolvement")
        inv = create_e_object_from_e_classifier(cls)
        inv.setInvolved(monitorar)
        cap.getOwnedAbstractFunctionAbstractCapabilityInvolvements().add(inv)

    def activity_allocation():
        cls = get_e_classifier("http://www.polarsys.org/capella/core/fa/" + version, "ComponentFunctionalAllocation")
        alloc = create_e_object_from_e_classifier(cls)
        motorista.getOwnedFunctionalAllocation().add(alloc)
        alloc.eSet(alloc.eClass().getEStructuralFeature("sourceElement"), motorista)
        alloc.eSet(alloc.eClass().getEStructuralFeature("targetElement"), monitorar)

    step("entity_involvements", entity_involvements)
    step("activity_involvement", activity_involvement)
    step("activity_allocation", activity_allocation)
    model.save()
    out["saved"] = True
    return out


try:
    _result = _probe()
except Exception as _exc:
    _result = {"probe": "seed", "probe_error": _err(_exc), "traceback": _traceback.format_exc()[-6000:]}
_result["stages"] = _REPORT["stages"]
_write_result(_result)
'''


def _with_params(params: dict, *parts: str) -> str:
    return "_PARAMS = " + repr(params) + "\n" + "".join(parts)


def delete_body(params: dict) -> str:
    """params: model_workspace_path, element_id, case, mechanism_name,
    mechanism (dict from candidates.MECHANISMS), em_lookups, progress_path."""
    return _with_params(params, _COMMON, _DELETE)


def verify_body(params: dict) -> str:
    """params: model_workspace_path, element_id, removed_ids, refresh,
    validate, progress_path."""
    return _with_params(params, _COMMON, _VERIFY)


def seed_body(params: dict) -> str:
    """params: model_workspace_path, ids {case: element_id}, progress_path."""
    return _with_params(params, _COMMON, _SEED)
