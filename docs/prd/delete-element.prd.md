# `delete_element`: delete a model element with Capella's semantic delete

**Status:** Draft, decisions confirmed 2026-10-09; needs the phase 1 spike
**Tracking issue:** [#4](https://github.com/FlavianMF/capella-mcp/issues/4)
**Depends on:** PR #5 (interim error text in `remove_from_diagram`, merged);
`impact_analysis` (`query.py`, PRD-09 query tools, merged)
**Client:** `capella_llm_window` (Capella chat plugin). Its follow-up is out of
scope here (see [Out of Scope](#out-of-scope)).

The seven open questions of the first draft were resolved with the user on
2026-10-09, and so was the plugin-side `delete_check` question; see
[Resolved (2026-10-09)](#resolved-2026-10-09).

## Problem

A user asked the chat assistant to delete an Operational Activity. The model
called `remove_from_diagram` on an Operational Activity Breakdown diagram and
got the breakdown refusal. That refusal pointed at `delete_diagram` +
`create_diagram`, which only hides the activity from one diagram. The user's
request could not be done: this server has no tool that deletes a model
element. The only delete tool is `delete_diagram`, and `remove_from_diagram`
removes a view node, never the element.

PR #5 changed the error text so the model now tells the user that no
model-delete tool exists. That stops the wrong tool call. It does not delete
anything. Today the user must leave the chat and delete the element by hand
in Capella, which breaks the "edit the model through the assistant" loop that
this server exists for (see `docs/index.md`, "Objetivo").

The cost of a bad delete is high. A raw EMF delete (`EcoreUtil.delete`, what
`delete()` in python4capella's `EMF_API.py` wraps, and what
`remove_from_diagram` uses for view nodes) removes the object but leaves
functional exchanges, involvements, allocations, traces and diagram views
pointing at nothing. Capella then shows broken diagrams or refuses to open the
model. So "add a delete" means "add Capella's own delete, with its cleanup".

## Evidence

- The incident in issue #4 (chat transcript in the plugin, Operational
  Activity on an OABD).
- Code: `src/capella_mcp/tools/model_tools.py` registers `delete_diagram` and
  `remove_from_diagram`, and no element delete. `bridge.remove_from_diagram`'s
  breakdown branch says so explicitly since PR #5.
- The plugin's system prompt already tells the model to call
  `impact_analysis` before deleting or changing an element
  (`ToolGuidanceSection.java`), and its permission engine already excludes
  `delete_*` from auto-approve (`PermissionEngine.java`). The client is ready
  for a delete tool; the server is the gap.
- Assumption, needs validation in the spike: Capella's semantic delete can be
  driven headless from a python4capella script without a confirmation dialog.

## Users and scenarios

- **Systems engineer refactoring an OA model in chat.** "Delete the activity
  *Monitorar velocidade do veículo*." The model calls `impact_analysis`, shows
  what will go (2 functional exchanges, 1 involvement in a capability, shown
  in 3 diagrams), the plugin asks for approval, the model calls
  `delete_element`, and answers with the returned list of removed items.
- **Same user, element with children.** "Delete *Veículo*." The entity owns
  *Painel de Instrumentos*. `impact_analysis` already reports
  `delete_check.reason: "has_children"` with `needs_cascade: true`, so the
  model asks the user before any write, then calls `delete_element` with
  `cascade=true`. Called without it, `delete_element` refuses and names the
  children.
- **Wrong target.** "Delete the root operational activity" or "delete the
  Operational Analysis". `impact_analysis` reports `reason: "protected"`;
  `delete_element` refuses the same way if called anyway.
- **Cleanup after a failed generation.** The model created a wrong element
  with `create_element` a moment ago and deletes it.

Job to be done: when I decide an element no longer belongs in the model, I
want to remove it and everything that only existed because of it, so that the
model stays valid and I don't have to clean it up by hand in Capella.

Non-users: scripts that need bulk deletes (many ids per call) and anything
that edits model files outside Capella (capellambse writes, see decision
0005).

## Goals / Non-goals

Goals:

- One write tool, `delete_element`, that removes one model element through
  Capella's semantic delete, in one transaction, and saves (spawn mode).
- No dangling references after a delete: exchanges, involvements,
  allocations, traces, owned children and diagram views that depend on the
  element are removed by Capella, not left behind.
- The result says exactly what was removed, with ids, so the model can answer
  from tool output and not guess.
- Clear refusals the LLM can act on: protected element, unsupported type,
  owns children without `cascade`, element not found.

Key hypothesis: we believe a semantic `delete_element` will let the chat
complete delete requests without manual cleanup in Capella. We will know when,
on the `car_hmi` fixture, (a) every supported element type deletes with zero
references to its id left in the `.capella`/`.aird` files, (b) every diagram
that showed it still opens and exports, and (c) replaying the issue #4 prompt
makes the model call `delete_element`, not `remove_from_diagram`.

Non-goals:

- Bulk delete (list of ids). One element per call keeps approval per element
  in the plugin and keeps results bounded.
- Undo. Spawn mode saves; recovery is the user's VCS or Capella's local
  history. Attach mode keeps Capella's own undo stack (the commit is a normal
  transaction in the user's session).
- Deleting diagrams (exists: `delete_diagram`) or view nodes (exists:
  `remove_from_diagram`).
- Moving or merging elements.

## Requirements

### Tool surface

```
delete_element(model_path: str, element_id: str, cascade: bool = False) -> dict
```

Registered in `tools/model_tools.py` next to `update_element`, wrapped in
`bridge.model_lock(...)` for the whole call like every other write tool.

There is no `dry_run`. The preview is the read-only `impact_analysis`, which
gains a `delete_check` field (R8). Reason: the plugin asks for approval on
every call of a write tool, a dry run included, while `impact_analysis` is a
READ tool and needs none.

Docstring must say, in this order: it deletes the element from the model (not
from one diagram); diagrams that show it lose its node; children and
dependent links go with it; call `impact_analysis` first to preview what goes
and whether the delete would be refused; `remove_from_diagram` is the tool to
only hide an element.

### Behaviour

| # | Requirement |
|---|-------------|
| R1 | Resolve `element_id` the same way `update_element` does (walk each layer's `get_all_contents()`, match `_element_id`). Not found -> `BridgeError("element not found: <id>")`. |
| R2 | Refuse protected elements, regardless of `cascade`: the `SystemEngineering`/project root, the five layer roots (`OperationalAnalysis`, `SystemAnalysis`, `LogicalArchitecture`, `PhysicalArchitecture`, `EPBSArchitecture`), every root package reached by a layer's `pkg_method`, and the root function/component/entity reached by `owned_root_method` (see `BREAKDOWN_DIAGRAMS` in `bridge.py`). |
| R3 | Refuse types outside the supported list for the current phase, with the list in the error text. Phase 2 list (confirmed 2026-10-09): OA layer only, the OA types `create_element` can make today (`OperationalActivity`, `OperationalEntity`, `OperationalActor`, `OperationalCapability`, OA `FunctionalExchange` ("Interaction")). Phase 5 adds `OperationalProcess`, `CommunicationMean` and the SA/LA/PA functions, components, capabilities and exchanges. |
| R4 | Cascade policy (confirmed 2026-10-09): if the element owns model elements other than its structural parts (ports, pins, constraints, property values, summaries), refuse unless `cascade=true`, and name the owned children (id, label, type, first 10 plus a count). Non-root packages the user made (e.g. an `OperationalActivityPkg` with content) follow the same rule: they need `cascade=true`, they are not refused outright. Links that point at the element (exchanges, involvements, allocations, traces) never trigger a refusal: semantic delete removes them and the result reports them. The plugin's approval prompt is the guard for those. |
| R5 | Delete with Capella's semantic delete inside `model.start_transaction()` / `commit_transaction()`, rolling back on any exception. Raw `EcoreUtil.delete` / `EMF_API.delete()` on a model element is forbidden. Which Capella entry point (candidates: `CapellaDeleteCommand` from `org.polarsys.capella.core.platform.sirius.ui.commands` with confirmation off, or the non-UI delete helper it delegates to) is decided by the spike (phase 1). |
| R6 | Save: spawn mode calls `model.save()`; attach mode does not (`if not _ATTACH_MODE: model.save()`, decision 0006). Dispatch through `_dispatch()` like `create_element`/`update_element`, so a live GUI session is used when one has the model open. |
| R7 | Result (actual, computed in the Capella process): `{deleted: true, element: {id, label, type}, removed: [{id, label, type, relation}], removed_count, diagrams_updated: [{uid, name}], diagrams_deleted: [{uid, name}], saved: bool}`. `relation` uses the `impact_analysis` vocabulary (`contained`, `exchange`, `trace`, `referenced_by`). `removed` is capped at 200 items with `truncated: true` and `removed_count` giving the total. Computed by snapshotting the element's containment tree and its inverse references before the delete, then keeping the ids that no longer resolve after commit. |
| R8 | Preview through `impact_analysis` (stays READ, no Capella process). Its result gains `delete_check: {allowed: bool, reason: null \| "not_found" \| "protected" \| "unsupported_type" \| "has_children", detail: str, needs_cascade: bool}`, computed on the capellambse fast path by the same R2-R4 rules `delete_element` applies. One shared helper (in `query.py` or a new `delete_policy.py`) holds the protected-root list, the type allowlist and the owned-children rule; the Capella-side template checks the same lists so the two answers agree. `needs_cascade` is true when the only obstacle is owned children. Like every fast-path read it reflects the last saved state. |
| R9 | Diagrams: diagrams that show the element are updated by the semantic delete (its node and connected edges go). Diagrams whose target (root) is the deleted element or one of its deleted children are deleted by Capella's cleanup; they are reported in `diagrams_deleted`. Breakdown diagrams re-synchronize on save, so their node disappears without extra code. All three are spike checks, not assumptions. |
| R10 | Fast-path reads after a delete must not see the element. `fast_reader._open` builds a new `MelodyModel` per call (no cache today), so no invalidation is needed; a regression test locks that in (P3). If a cache is added later, `delete_element` must invalidate it. In attach mode the delete is not saved, so fast-path reads (and `impact_analysis`) still see the element until the user saves; the docstring says so. |

### Error texts the LLM sees

Every refusal is a `BridgeError` (MCP `is_error=true`) and says what to do
next.

| Case | Text (shape) |
|------|------|
| Not found | `element not found: <id>. Use list_elements or get_element to find the current id.` |
| Protected | `<label> (<type>) is a protected <layer root / root package / root function> and cannot be deleted. Delete its children instead; impact_analysis on a child shows whether it can be deleted.` |
| Unsupported type | `delete_element does not support <type> yet. Supported: <list>. Tell the user it must be deleted in Capella.` |
| Has children, no cascade | `<label> owns <n> elements (<first ids/labels>). Call impact_analysis to show the user everything that would go, ask them, then call delete_element again with cascade=true.` |
| Delete failed | `Capella refused the delete of <label>: <cause>. Nothing was changed (transaction rolled back).` |

`remove_from_diagram`'s breakdown refusal (PR #5) changes from "this server
does not provide [a model-delete tool] yet" to "use delete_element".

## Design notes

- **Why semantic delete, not raw EMF.** `remove_from_diagram` can use
  `EcoreUtil.delete` because it deletes Sirius view objects whose only
  dependants are edges, which it deletes first. A model element has
  cross-references in other fragments (exchanges, involvements, traces,
  `DSemanticDecorator.target` in the `.aird`). Capella's delete command walks
  those through its own rules (e.g. a functional exchange goes when its port
  goes). Re-implementing that in a template would drift from Capella.
- **Why not capellambse.** Decision 0005: capellambse cannot write safely next
  to Sirius diagrams. It stays the read path (R8, R10).
- **Why the result is computed in Capella, not taken from `impact_analysis`.**
  `impact_analysis` runs on the saved files in another process and is capped;
  it is a preview. The authoritative list is what is gone after commit. The
  vocabulary is shared so the model can compare preview and result.
- **Fixed template only.** Like every other tool: no generated Python, ids
  passed with `repr()` (CLAUDE.md, "Não gerar/injetar código Python
  arbitrário").
- **Plugin interaction.** The plugin treats any tool outside its `READ_TOOLS`
  as a write: always asks for approval in every non-bypass mode, and
  `delete_*` is excluded from auto-approve. Its dirty-editor guard
  (`McpWriteGuard`) makes the user save before the call, and it refreshes the
  project after a write, so the GUI reloads the saved files. That is why the
  preview lives in `impact_analysis` (in the plugin's `READ_TOOLS`, no
  approval) and not in a `dry_run` flag on `delete_element`, which the plugin
  would gate like a real delete.
- **`delete_check` and the shared query contract.** `impact_analysis` is a
  PRD-09 tool whose result shape is shared with the plugin's live Java
  backend. `delete_check` is a new optional field: clients that don't know it
  ignore it. The live backend fills it too, with the same rules (decided
  2026-10-09, see [Resolved](#resolved-2026-10-09) item 8).

## Security

- Path handling unchanged: `resolve_model_path` rejects escapes from
  `MODELS_ROOT`.
- `element_id` reaches the template through `repr()` only.
- Destructive, irreversible in spawn mode: the server relies on the client's
  approval prompt. The server-side guards are R2 (protected), R3 (allowlist)
  and R4 (cascade opt-in).

## Testing

| Level | What | Where |
|-------|------|-------|
| Unit (mocked `_dispatch`/`_run_script`) | template contains the transaction, rollback and `_ATTACH_MODE` save guard; refusal paths map to `BridgeError` with the texts above; tool is registered with the right signature | `tests/test_bridge.py`, `tests/test_server.py` |
| Unit (capellambse, `rich_model`) | `impact_analysis` `delete_check`: protected roots (each kind), unsupported type, owns children (`needs_cascade: true`), user package with content needs cascade, links alone do not refuse, allowed element; existing `impact_analysis` fields unchanged | `tests/test_query.py` (and `tests/test_delete_policy.py` if the helper gets its own module) |
| Parity (live Capella) | for each refusal case, `impact_analysis`'s `delete_check.reason` matches the refusal `delete_element` raises | `tests/test_integration.py` |
| Integration (live Capella, skipped without `CAPELLA_BIN`, car_hmi fixture) | per supported type: delete, then `get_element` raises not found on both fast and headless paths; the uuid appears nowhere in the `.capella`/`.aird` text; every diagram that showed it still `get_diagram`s and exports a non-blank PNG; cascade refusal then cascade success on *Veículo*; protected refusal on the root activity; OABD node gone after delete | `tests/test_integration.py` |
| Fixture hygiene | integration tests work on a copy of `car_hmi` (temp dir under `MODELS_ROOT`), never on the committed fixture, since the delete saves | `tests/conftest.py` / integration setup |
| LLM replay | issue #4 prompt against the plugin with a local model: tool chosen is `delete_element` | manual, `docs/testing-with-llm.md` |

## Implementation phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | Live spike | Throwaway script on a copy of `car_hmi`: find the headless semantic-delete entry point, check cleanup per OA type, diagrams (OABD resync, OAB/OAIB nodes and edges, diagrams rooted at the element), attach mode, and that no dialog blocks | kit ready ([guide](../spikes/delete-element-spike.md)), awaiting a live run | - | - | - |
| 2 | `delete_element` for OA | Bridge template + tool, R1-R10 for the OA allowlist, shared delete-policy helper, `delete_check` in `impact_analysis`, unit tests, `remove_from_diagram` text update | pending | - | 1 | - |
| 3 | Integration tests | Live tests on a fixture copy, per OA type, plus the fixture-copy helper | pending | with 4 | 2 | - |
| 4 | Docs | `docs/architecture.md` tool list, decision record for "semantic delete, not EMF" and the cascade policy, README tool table | pending | with 3 | 2 | - |
| 5 | Other layers | Extend the allowlist to SA/LA/PA (functions, components, capabilities, exchanges), one spike check + integration test per type | pending | - | 3 | - |

### Phase details

**Phase 1: Live spike**
- Goal: replace the assumptions in R5 and R9 with observed behaviour, the way
  `remove_from_diagram` was spiked on 2026-09-04 (see its docstring).
- Scope: notes in the PR description and in `bridge.py`'s docstring for
  `delete_element`; no merged tool.
- Success signal: for each OA type, a written list of what Capella removed,
  which diagrams changed or were deleted, and the uuid absent from both files
  after save. A go/no-go on attach mode.

**Phase 2: `delete_element` for OA**
- Goal: the tool works for the incident's case and its OA neighbours.
- Scope: `bridge.delete_element`; the shared delete-policy helper
  (protected roots, OA allowlist, owned-children rule); `delete_check` added
  to `query.impact_analysis` and its tool docstring in `query_tools.py`;
  tool in `model_tools.py`; unit tests; PR #5 text update.
- Success signal: `uv run pytest tests/ -k "not integration"` green; tool
  listed by the MCP Inspector.

**Phase 3: Integration tests**
- Goal: lock the spike's findings in, so a Capella upgrade that changes
  delete behaviour fails a test.
- Success signal: `scripts/run_integration_tests.sh` green with the new tests;
  skipped (not failed) without Capella.

**Phase 4: Docs**
- Goal: the tool and its policy are discoverable without reading `bridge.py`.
- Success signal: `docs/architecture.md` lists the tool and the
  `delete_check` field of `impact_analysis`; a new `docs/decisions/0007-...`
  records semantic delete, the cascade rule, and why the preview is in
  `impact_analysis` and not a `dry_run` flag.

**Phase 5: Other layers**
- Goal: same behaviour for SA/LA/PA.
- Success signal: allowlist grown, one integration test per added type.

### Parallelism notes

3 and 4 both need only the merged tool and touch different files. 5 waits for
3 so the per-type test pattern exists before it is repeated.

## Decisions log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| Delete mechanism | Capella semantic delete in a transaction | raw `EcoreUtil.delete`; capellambse write | raw leaves dangling refs; capellambse is read-only by decision 0005 |
| Preview | `delete_check` in the read-only `impact_analysis` (user, 2026-10-09) | `dry_run` flag on `delete_element`; headless dry run with rollback | the plugin asks approval for every write-tool call, a dry run included; `impact_analysis` is READ |
| Children | refuse without `cascade=true`; user packages with content follow the same rule (user, 2026-10-09) | always cascade; never cascade; refuse packages outright | owned children are the surprising loss; links are expected |
| Scope v1 | OA layer only (user, 2026-10-09) | all layers at once | incident is OA, car_hmi fixture is OA-rich, each type needs a live check |
| Granularity | one id per call | list of ids | approval per element in the plugin, bounded result |
| Dispatch | `_dispatch()` (attach-aware) with `_ATTACH_MODE` save guard; spawn-only fallback if the spike finds a UI-thread or dialog problem (user, 2026-10-09) | `_run_script()` only, like the diagram tools | same as `create_element`/`update_element` |

## Resolved (2026-10-09)

The user confirmed all seven first-draft questions ("all ok") and accepted
the change to question 2.

1. **Cascade policy.** Confirmed: refuse when the element owns non-structural
   children, unless `cascade=true`; links never refuse (R4).
2. **Preview: `dry_run` dropped (changed).** `delete_element` has no
   `dry_run`. The read-only `impact_analysis` reports whether
   `delete_element` would be refused and why (`delete_check`, R8): protected
   element, unsupported type, or owns children without cascade. Reason: the
   plugin asks for approval on every call of a write tool, a dry run
   included; `impact_analysis` is READ and needs none.
3. **First-release types.** Confirmed: OA layer only (R3).
4. **Diagrams rooted at the deleted element.** Confirmed: Capella deletes
   them and the result lists them in `diagrams_deleted` (R9).
5. **Attach mode.** Confirmed: supported through `_dispatch()`, no forced
   save. If the spike shows the delete needs the UI thread or opens a dialog
   in a live GUI, fall back to spawn-only (`_run_script()`) like the diagram
   tools (R6).
6. **Protected list.** Confirmed: model root, five layer roots, root
   packages, root function/component/entity (R2). Sub-question resolved as
   the default under the same "all ok": a user-made package with content
   needs `cascade=true`, the same rule as any other container; it is not
   refused outright (R4).
7. **Result cap.** Confirmed: 200 items in `removed`, the same max as the
   query tools (R7).
8. **`delete_check` on the plugin's live backend (decided 2026-10-09).**
   The plugin's live Java backend for `impact_analysis` fills
   `delete_check` too, with the same rules (protected roots, OA allowlist,
   owned-children / `needs_cascade`), and the plugin's copy of the shared
   PRD-09 query contract lists the field. So `delete_check` is part of the
   shared `impact_analysis` shape, not a field only this server fills. Both
   sides must keep the rule lists in sync. The parity test (live
   `delete_check.reason` vs the refusal `delete_element` raises) covers
   this server only.

## Open questions

None left from the first draft. The plugin-side `delete_check` question
was decided on 2026-10-09 ([Resolved](#resolved-2026-10-09) item 8). The
phase 1 spike answers R5/R9/R6 (kit and results template:
[`docs/spikes/delete-element-spike.md`](../spikes/delete-element-spike.md)).

## Out of scope

- **Plugin follow-up** (`capella_llm_window`): bump the `capella_mcp`
  submodule; update `ToolGuidanceSection` so the delete line names
  `delete_element` and says to call `impact_analysis` first; check that
  `ModeSection` still says deletes always ask. No change to
  `McpWriteGuard.READ_TOOLS` or `PermissionEngine` is needed: `delete_element`
  is a write and matches `delete_*`.
- Bulk delete, undo/restore, move/merge.
- Deleting EPBS elements, data model (`DataPkg`/`Class`) elements and
  scenario contents (`InstanceRole`, `SequenceMessage`); each needs its own
  spike.
- A "save now" tool for attach mode (decision 0006 left it out).

## Research summary

Codebase:

- Write tools: `bridge.update_element` (element lookup, transaction,
  `_ATTACH_MODE` save guard, `_dispatch`) is the template to copy.
  `bridge.delete_diagram` shows calling a Capella/Sirius Java API by its full
  name inside a template (`DialectManager.INSTANCE.deleteRepresentation`).
- `bridge.remove_from_diagram`: raw `delete()` on view objects, live spike
  notes in its docstring, breakdown refusal text from PR #5.
- `query.impact_analysis`: contained / referenced_by / trace / exchange /
  diagram walk on capellambse, `counts` before the cap.
- `fast_reader._open`: new `MelodyModel` per call, no cache.
- `bridge.model_lock`: file lock that tools take around every write.
- Tests: `tests/test_bridge.py` mocks the script runner;
  `tests/test_integration.py` runs live, skipped without `CAPELLA_BIN`.
- Plugin: `McpWriteGuard.READ_TOOLS`, `PermissionEngine` (no auto-approve for
  `delete_*`), `ToolGuidanceSection` (`impact_analysis` before deleting).

Market: not researched for this PRD. Capella's own GUI delete (with its
"elements to delete" confirmation dialog) is the reference behaviour the tool
should match.

---

*Generated: 2026-10-09. Status: DRAFT, needs the phase 1 spike.*
