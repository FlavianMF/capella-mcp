# `delete_element`: delete a model element with Capella's semantic delete

**Status:** Draft
**Tracking issue:** [#4](https://github.com/FlavianMF/capella-mcp/issues/4)
**Depends on:** PR #5 (interim error text in `remove_from_diagram`, merged);
`impact_analysis` (`query.py`, PRD-09 query tools, merged)
**Client:** `capella_llm_window` (Capella chat plugin). Its follow-up is out of
scope here (see [Out of Scope](#out-of-scope)).

Defaults marked **(default, not confirmed)** are recommendations made while
writing this PRD. Each one is listed again in [Open Questions](#open-questions).

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
  *Painel de Instrumentos*. `delete_element` refuses without `cascade=true`
  and says which children it owns. The model asks the user, then calls again
  with `cascade=true`.
- **Wrong target.** "Delete the root operational activity" or "delete the
  Operational Analysis". `delete_element` refuses: protected element.
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
delete_element(model_path: str, element_id: str, cascade: bool = False, dry_run: bool = False) -> dict
```

Registered in `tools/model_tools.py` next to `update_element`, wrapped in
`bridge.model_lock(...)` for the whole call like every other write tool.

Docstring must say, in this order: it deletes the element from the model (not
from one diagram); diagrams that show it lose its node; children and
dependent links go with it; call `impact_analysis` or `dry_run=true` first;
`remove_from_diagram` is the tool to only hide an element.

### Behaviour

| # | Requirement |
|---|-------------|
| R1 | Resolve `element_id` the same way `update_element` does (walk each layer's `get_all_contents()`, match `_element_id`). Not found -> `BridgeError("element not found: <id>")`. |
| R2 | Refuse protected elements, regardless of `cascade`: the `SystemEngineering`/project root, the five layer roots (`OperationalAnalysis`, `SystemAnalysis`, `LogicalArchitecture`, `PhysicalArchitecture`, `EPBSArchitecture`), every root package reached by a layer's `pkg_method`, and the root function/component/entity reached by `owned_root_method` (see `BREAKDOWN_DIAGRAMS` in `bridge.py`). |
| R3 | Refuse types outside the supported list for the current phase, with the list in the error text. Phase 2 list (default, not confirmed): OA layer only, the OA types `create_element` can make today (`OperationalActivity`, `OperationalEntity`, `OperationalActor`, `OperationalCapability`, OA `FunctionalExchange` ("Interaction")). `OperationalProcess` and `CommunicationMean` follow in phase 5 with the other layers. Phase 4 adds SA/LA/PA functions, components, capabilities and exchanges. |
| R4 | Cascade policy (default, not confirmed): if the element owns model elements other than its structural parts (ports, pins, constraints, property values, summaries), refuse unless `cascade=true`, and name the owned children (id, label, type, first 10 plus a count). Links that point at the element (exchanges, involvements, allocations, traces) never trigger a refusal: semantic delete removes them and the result reports them. The plugin's approval prompt is the guard for those. |
| R5 | Delete with Capella's semantic delete inside `model.start_transaction()` / `commit_transaction()`, rolling back on any exception. Raw `EcoreUtil.delete` / `EMF_API.delete()` on a model element is forbidden. Which Capella entry point (candidates: `CapellaDeleteCommand` from `org.polarsys.capella.core.platform.sirius.ui.commands` with confirmation off, or the non-UI delete helper it delegates to) is decided by the spike (phase 1). |
| R6 | Save: spawn mode calls `model.save()`; attach mode does not (`if not _ATTACH_MODE: model.save()`, decision 0006). Dispatch through `_dispatch()` like `create_element`/`update_element`, so a live GUI session is used when one has the model open. |
| R7 | Result (actual, computed in the Capella process): `{deleted: true, element: {id, label, type}, removed: [{id, label, type, relation}], removed_count, diagrams_updated: [{uid, name}], diagrams_deleted: [{uid, name}], saved: bool}`. `relation` uses the `impact_analysis` vocabulary (`contained`, `exchange`, `trace`, `referenced_by`). `removed` is capped at 200 items with `truncated: true` and `removed_count` giving the total. Computed by snapshotting the element's containment tree and its inverse references before the delete, then keeping the ids that no longer resolve after commit. |
| R8 | `dry_run=true`: no Capella process, no write, no lock beyond the read lock. Runs the R1-R4 checks on the capellambse fast path and returns `{deleted: false, dry_run: true, would_refuse: <reason or null>, impact: <impact_analysis result>}`. It reflects the last saved state, like every fast-path read. |
| R9 | Diagrams: diagrams that show the element are updated by the semantic delete (its node and connected edges go). Diagrams whose target (root) is the deleted element or one of its deleted children are deleted by Capella's cleanup; they are reported in `diagrams_deleted`. Breakdown diagrams re-synchronize on save, so their node disappears without extra code. All three are spike checks, not assumptions. |
| R10 | Fast-path reads after a delete must not see the element. `fast_reader._open` builds a new `MelodyModel` per call (no cache today), so no invalidation is needed; a regression test locks that in (P3). If a cache is added later, `delete_element` must invalidate it. In attach mode the delete is not saved, so fast-path reads (and `impact_analysis`) still see the element until the user saves; the docstring says so. |

### Error texts the LLM sees

Every refusal is a `BridgeError` (MCP `is_error=true`) and says what to do
next.

| Case | Text (shape) |
|------|------|
| Not found | `element not found: <id>. Use list_elements or get_element to find the current id.` |
| Protected | `<label> (<type>) is a protected <layer root / root package / root function> and cannot be deleted. Delete its children instead.` |
| Unsupported type | `delete_element does not support <type> yet. Supported: <list>. Tell the user it must be deleted in Capella.` |
| Has children, no cascade | `<label> owns <n> elements (<first ids/labels>). Ask the user, then call delete_element again with cascade=true to delete them too.` |
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
  project after a write, so the GUI reloads the saved files. `dry_run=true`
  is still a write-kind call for the plugin (same tool name); the model
  should prefer `impact_analysis` (a READ) for previews. Documented in the
  docstring.

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
| Unit (capellambse, `rich_model`) | `dry_run` policy checks: protected roots, unsupported type, owns children, links do not refuse | `tests/test_query.py` or new `tests/test_delete_policy.py` |
| Integration (live Capella, skipped without `CAPELLA_BIN`, car_hmi fixture) | per supported type: delete, then `get_element` raises not found on both fast and headless paths; the uuid appears nowhere in the `.capella`/`.aird` text; every diagram that showed it still `get_diagram`s and exports a non-blank PNG; cascade refusal then cascade success on *Veículo*; protected refusal on the root activity; OABD node gone after delete | `tests/test_integration.py` |
| Fixture hygiene | integration tests work on a copy of `car_hmi` (temp dir under `MODELS_ROOT`), never on the committed fixture, since the delete saves | `tests/conftest.py` / integration setup |
| LLM replay | issue #4 prompt against the plugin with a local model: tool chosen is `delete_element` | manual, `docs/testing-with-llm.md` |

## Implementation phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | Live spike | Throwaway script on a copy of `car_hmi`: find the headless semantic-delete entry point, check cleanup per OA type, diagrams (OABD resync, OAB/OAIB nodes and edges, diagrams rooted at the element), attach mode, and that no dialog blocks | pending | - | - | - |
| 2 | `delete_element` for OA | Bridge template + tool, R1-R10 for the OA allowlist, unit tests, `remove_from_diagram` text update | pending | - | 1 | - |
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
- Scope: `bridge.delete_element`, `bridge._delete_element_dry_run` (fast
  path), protected-root helper shared with the dry run, tool in
  `model_tools.py`, unit tests, PR #5 text update.
- Success signal: `uv run pytest tests/ -k "not integration"` green; tool
  listed by the MCP Inspector.

**Phase 3: Integration tests**
- Goal: lock the spike's findings in, so a Capella upgrade that changes
  delete behaviour fails a test.
- Success signal: `scripts/run_integration_tests.sh` green with the new tests;
  skipped (not failed) without Capella.

**Phase 4: Docs**
- Goal: the tool and its policy are discoverable without reading `bridge.py`.
- Success signal: `docs/architecture.md` lists the tool; a new
  `docs/decisions/0007-...` records semantic delete and the cascade default.

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
| Preview | `dry_run` on the fast path, plus existing `impact_analysis` | `impact_analysis` only; headless dry run with rollback | cheap, no Capella process; but see open question 2 |
| Children | refuse without `cascade=true` (default, not confirmed) | always cascade; never cascade | owned children are the surprising loss; links are expected |
| Scope v1 | OA layer only (default, not confirmed) | all layers at once | incident is OA, car_hmi fixture is OA-rich, each type needs a live check |
| Granularity | one id per call | list of ids | approval per element in the plugin, bounded result |
| Dispatch | `_dispatch()` (attach-aware) with `_ATTACH_MODE` save guard (default, not confirmed) | `_run_script()` only, like the diagram tools | same as `create_element`/`update_element`; spike must confirm attach |

## Open questions

Each has a default this PRD uses; none is confirmed by the user.

1. **Cascade policy.** Default: refuse when the element owns non-structural
   children, unless `cascade=true`; links never refuse. Alternative: refuse
   on any inbound reference too (safer, but almost every element has one, and
   the plugin already asks for approval).
2. **Keep `dry_run`?** Default: yes, on the fast path. Against: `impact_analysis`
   already previews, and in the plugin `dry_run` is a write-kind call that
   asks for approval. Option: drop `dry_run` and add the policy fields
   (`would_refuse`) to `impact_analysis` instead.
3. **First-release type list.** Default: OA layer only (R3). Alternative: all
   types the spike verifies in phase 1.
4. **Diagrams rooted at the deleted element.** Default: let Capella delete
   them and report them in `diagrams_deleted`. Alternative: refuse unless
   `cascade=true`, because losing a whole diagram may surprise the user more
   than losing an exchange.
5. **Attach mode.** Default: supported through `_dispatch()`, no forced save.
   Risk: the semantic delete may need the UI thread or open a dialog in a live
   GUI. If the spike finds that, fall back to spawn-only (`_run_script()`)
   like the diagram tools.
6. **Protected list.** Default: model root, five layer roots, root packages,
   root function/component/entity. Open: should non-root packages with
   content (e.g. a user-made `OperationalActivityPkg`) need `cascade` (yes,
   by R4) or be refused outright?
7. **Result cap.** Default: 200 items in `removed`, same max as the query
   tools.

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
