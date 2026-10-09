# `delete_element` phase 1 spike: how to run it

Phase 1 ("live spike") of [`docs/prd/delete-element.prd.md`](../prd/delete-element.prd.md).
Goal: replace the assumptions in R5 (which Capella entry point deletes
semantically, headless, with no dialog) and R9 (what happens to diagrams)
with observed behaviour, per OA type, and give a go/no-go on attach mode.

The kit lives in [`spikes/delete_element/`](../../spikes/delete_element/). It
was written on a machine without Capella, so **nothing in it has run
against Capella yet**. The class names it tries are educated guesses from
Capella/Sirius/EMF sources, and the first step checks them against your
install. Expect one round of fixes after the first run. Every Java call is
wrapped and its error recorded, so a failed run still tells us what to
change.

## What it does

1. **Seed** (once per results dir). Copies `tests/fixtures/car_hmi` to
   `<out>/seeded/` and adds what car_hmi's OA layer lacks: two entity
   involvements and one activity involvement on the capability, an activity
   allocation (Motorista -> Monitorar), one OA interaction (Monitorar ->
   Fornecer, owned by Exibir), and five diagrams: `Spike OAIB`,
   `Spike OEB`, `Spike OCB`, `Spike OABD rooted at Monitorar`,
   `Spike OEBD rooted at Veiculo`. The committed OABD
   *Atividades de Velocidade do Veículo* (rooted at Exibir, shows Monitorar
   and Fornecer) is already there. The seed uses the bridge's own
   `create_element`/`create_*diagram` tools. A failed step is recorded, not
   fatal.
2. **Per (mechanism, case)**: a fresh copy of the seeded model in
   `<out>/runs/<mechanism>__<case>/models/`, then:
   - scans `.capella`/`.aird`/`.afm` before (pure Python, `model_files.py`);
   - **delete probe** (one headless Capella through `bridge._run_script`):
     records the thread, whether it is the SWT UI thread and whether a
     workbench runs; snapshots the element's containment tree, its inverse
     references (session cross-referencer) and every diagram; runs the
     mechanism; then records what no longer resolves, references still
     pointing at detached objects, diagrams deleted and dangling views; then
     saves;
   - scans the files again: ids removed, **new** dangling references,
     whether the uuid still appears anywhere, which diagrams were deleted or
     lost views;
   - **verify probe** (fresh Capella): reopens, checks the removed ids are
     gone, runs EMF `Diagnostician` on the semantic roots, refreshes every
     diagram (this is what re-syncs breakdown diagrams), saves; files scanned
     again;
   - **export** (`bridge.export_diagram`): every diagram to PNG; a PNG of
     200 bytes or less counts as blank.
3. Writes `results.json` + `results.md` after every run, so an interrupted
   matrix still leaves results.

The fixture in `tests/fixtures/` is only ever read. Every write happens on a copy.

### Mechanisms (`spikes/delete_element/candidates.py`)

| id | what | role |
|---|---|---|
| `capella_cmd_in_tx` | `CapellaDeleteCommand` (the GUI's Delete), confirmation and steps off, ensureTransaction off, `run()` inside python4capella's `start_transaction`/`commit_transaction` | the R5 target shape |
| `capella_cmd_em` | same command, executed via its `ExecutionManager` (own transaction) | fallback if the first can't nest |
| `delete_structure_in_tx` | `DeleteStructureCommand` (non-UI helper in `core.model.handler`), `canExecute()` + `execute()` in the transaction | the "non-UI helper" R5 mentions |
| `emf_delete_command_in_tx` | EMF Edit `DeleteCommand.create(domain, [el])` | control: generic cross-ref cleanup, no Capella rules |
| `sirius_util_delete_in_tx` | `SiriusUtil.delete(el, session)` | control: Sirius cleanup, no Capella rules |
| `ecoreutil_delete` | `EcoreUtil.delete(el, true)` | negative control (forbidden by R5): what dangling looks like |

Booleans in the commands' constructors are set by name: the probe builds
throwaway instances to learn which field each boolean sets, then turns off
anything named `confirm*`, `step*` and `prompt*`. The mapping it found is
in each run's report.

### Cases (car_hmi ids, stable across copies)

| case | element | type | why |
|---|---|---|---|
| `oa_leaf_in_oabd` | Monitorar velocidade do veículo | OperationalActivity | the issue #4 shape: node in the OABD; seeded interaction source, involvement, allocation; root of a seeded OABD |
| `oa_with_children` | Exibir velocidade do veículo | OperationalActivity | owns 2 activities + the interaction; root of the committed OABD |
| `entity_with_child` | Veículo | OperationalEntity | owns Painel de Instrumentos; involved in the capability; root of the seeded OEBD |
| `actor` | Motorista | OperationalActor | involvement + allocation |
| `capability` | Visualizar velocidade do veículo | OperationalCapability | owns the involvements; node in the OCB |
| `functional_exchange` | Spike Interaction (seeded) | FunctionalExchange | edge in the OAIB; do its ports go too? |
| `root_activity` | Root Operational Activity | OperationalActivity | R2 protected root: does Capella itself refuse? |

Default plan: the three candidate mechanisms on all 7 cases, plus the three
controls on `oa_leaf_in_oabd` only, for 24 runs. `--quick` runs just
`capella_cmd_in_tx` and `ecoreutil_delete` on `oa_leaf_in_oabd`.

## Prerequisites

- The machine with **Capella 7.0.x + python4capella 1.4.1** (the versions
  the Dockerfile pins), headless-capable: `xvfb-run` on `PATH`. Or Docker
  with the `capella-mcp` image (see [Docker](#docker-alternative)).
- This branch checked out: `git fetch origin && git switch spike/delete-element`.
- `uv` (the repo's tool). `uv sync` once.
- No Capella GUI needs to be open for the headless part. For the attach
  part you need the GUI with the attach listener registered
  (`docs/architecture.md`, "attach mode", `scripts/register_attach_listener.py`).

## Steps

All commands run from the repo root. Set your Capella launcher once:

```bash
export CAPELLA_BIN=/path/to/capella/capella   # the launcher, next to plugins/
```

**0. Unit tests (no Capella, seconds).**

```bash
uv run pytest spikes/delete_element -q
```

**1. Discovery (no Capella process, seconds).** Lists every `*Delet*` class
in the Capella/Sirius/EMF bundles and checks the guessed class names.

```bash
uv run python spikes/delete_element/run_spike.py discover --out spike-results/delete_element/discovery
```

It prints each mechanism with `found`, `bundle_corrected`,
`class_corrected` or `not_found`. `run` does this itself and applies the
corrections. This step is only to see them early.

**2. Quick run (about 10 to 15 min).** Seeding (about 10 to 15 Capella
launches, because the diagram tools are multi-pass) plus 2 runs (3
launches each: delete, verify, export).

```bash
uv run python spikes/delete_element/run_spike.py run --quick --out spike-results/delete_element/r1
```

If `capella_cmd_in_tx` comes back `unavailable`, `exception` or `timeout`,
stop and send `spike-results/delete_element/r1/results.md` (see
[What to send back](#what-to-send-back)). The full matrix would repeat the same failure.

**3. Full matrix (about 45 to 90 min).** About 24 runs x 3 launches at
30 to 60 s per launch. Reusing the same `--out` reuses the seeded copy.
`--no-export` saves about a third of the time if PNGs are not needed this round.

```bash
uv run python spikes/delete_element/run_spike.py run --out spike-results/delete_element/r1
```

Useful flags: `--mechanisms a,b`, `--cases x,y` (full cross product of the
two), `--timeout 300` (per Capella call), `--no-verify`, `--reseed`,
`--candidates my.json` (see [Fixing a candidate](#fixing-a-candidate)).

**4. Attach mode (manual, about 10 min per case).** Needed for the R6
go/no-go. Uses a separate results dir, because the GUI holds the project
open.

```bash
uv run python spikes/delete_element/run_spike.py attach-prepare --out spike-results/delete_element/attach1
```

Follow what it prints: import the printed folder as an existing project
**without copying**, open `car_hmi.aird` and the OABD *Atividades de
Velocidade do Veículo*, and check the listener heartbeat lists the model.
Then:

```bash
uv run python spikes/delete_element/run_spike.py attach-run --out spike-results/delete_element/attach1 \
    --mechanism capella_cmd_in_tx --case oa_leaf_in_oabd
```

Watch the GUI while it runs. It then asks you: did a dialog appear, did the
GUI update, does Edit > Undo offer the delete (don't click it), is the
model dirty. Next it asks you to save (Ctrl+S) and press Enter, then
re-scans the files. Run each further case against a new `--out`
(`attach2`, ...), after removing the previous project from the GUI
workspace. If the headless matrix chose a mechanism other than
`capella_cmd_in_tx`, pass that one.

A `timeout` here (default 120 s) with last stage `executing` means the
listener thread was blocked. That usually means a dialog or a wait on the UI thread.

### Docker alternative

The image has Capella 7.0.1 at `/opt/capella/capella` and `xvfb-run`
(headless part only. Attach needs your own GUI):

```bash
docker build -t capella-mcp .
mkdir -p spike-results
docker run --rm \
    -v "$(pwd)/spikes:/app/spikes" -v "$(pwd)/tests:/app/tests" -v "$(pwd)/spike-results:/app/spike-results" \
    --entrypoint bash capella-mcp \
    -c "uv sync --frozen && uv run python spikes/delete_element/run_spike.py run --quick \
        --capella-bin /opt/capella/capella --out /app/spike-results/delete_element/r1"
sudo chown -R "$USER": spike-results
```

## Where results land

```
spike-results/delete_element/<out>/        # git-ignored
  results.md / results.json                 # summary table + per-run details
  discovery.json                            # every *Delet* class found, with bundle names
  seeded/seed_info.json                     # seed steps (ok/error), seeded interaction id
  runs/<mechanism>__<case>/run.json         # one run, full detail
  runs/<mechanism>__<case>/models/car_hmi/  # the model after delete + verify (open it in Capella if in doubt)
  runs/<mechanism>__<case>/*.progress.json  # last stage reached (matters after a timeout)
  attach_results.md / .json                 # attach runs (attach-* only)
  workspaces/                               # throwaway Eclipse workspaces, ignore
```

`python spikes/delete_element/report.py <out>/results.json` regenerates
`results.md` from the json.

### Reading the summary

| outcome | meaning |
|---|---|
| `deleted` | the mechanism ran, committed, and the element is detached |
| `exception` | it threw while running. The transaction was rolled back (see `delete_exception`) |
| `unavailable` | class, bundle, constructor or ExecutionManager not found before touching the model. Later cases of that mechanism are skipped |
| `timeout` | the Capella process didn't finish in `--timeout`. Check the last stage: `executing` means it blocked inside the mechanism (dialog or UI thread) |
| `no_effect` | ran without error but the element is still attached |
| `not_found` / `seed_missing` | the case's element isn't in the copy (a seed step failed) |

Columns that answer the PRD directly: `uuid gone from files` (hypothesis
a), `new dangling refs` after delete and after reopen+refresh (must be 0),
`still shown in` (R9 node gone, before and after a refresh), `diagrams
deleted` (R9 rooted diagrams), `UI thread`/`workbench` (headless without
UI), `blank PNGs` (hypothesis b. `Spike OEB`/`Spike OEBD` export blank
anyway, a known OperationalEntity limitation in `bridge.py`, so don't
count them).

### Fixing a candidate

If discovery shows the real class under another name or constructor shape:

```bash
uv run python -c "import json,sys; sys.path.insert(0,'spikes/delete_element'); import candidates; \
json.dump(candidates.MECHANISMS, open('my-candidates.json','w'), indent=2)"
# edit bundle/class/bool_rules/invoke in my-candidates.json, then
uv run python spikes/delete_element/run_spike.py run --candidates my-candidates.json \
    --mechanisms capella_cmd_in_tx --cases oa_leaf_in_oabd --out spike-results/delete_element/r2
```

## What to send back

1. `results.md` of the full run (and of the quick run if the full one
   wasn't run).
2. `discovery.json` if any mechanism was `unavailable`, or the discovery
   notes at the top of `results.md`.
3. For each `timeout`/`exception` on a `capella_*` or `delete_structure_*`
   mechanism, that run's `run.json`.
4. `attach_results.md`, plus anything you saw in the GUI that the prompts
   didn't cover (Error Log entries, dialog text).

Or one archive: `tar czf delete-spike.tgz --exclude=workspaces -C spike-results delete_element`.

## Results template (fill from the results, one row per PRD phase 1 question)

Copy this into the PR description or the PRD's phase 1 notes once you have results.

| # | Question (PRD ref) | Answer | Evidence (run) | Consequence for phase 2 |
|---|---|---|---|---|
| Q1 | Which entry point does Capella's semantic delete headless, from a python4capella script? Class, bundle, constructor, boolean mapping (R5) | | | template for `bridge.delete_element` |
| Q2 | Does it run inside python4capella's `start_transaction`/`commit_transaction`, with rollback on error? (R5) | | | in-tx vs ExecutionManager |
| Q3 | No confirmation dialog, no UI thread needed in spawn mode? (thread, `on_ui_thread`, `workbench_running`, no timeout) (R5, Evidence "assumption") | | | |
| Q4 | OperationalActivity (leaf, issue #4): what was removed (ports, interaction, involvement, allocation)? New dangling refs = 0? (R4, R7) | | | |
| Q5 | OperationalActivity with children: children and owned interaction removed with it? (cascade semantics, R4) | | | |
| Q6 | OperationalEntity with child / OperationalActor: involvements, allocations, child entity removed? (R3, R4) | | | |
| Q7 | OperationalCapability: involvements removed, entities/activities kept? (R3) | | | |
| Q8 | FunctionalExchange: exchange and its two ports removed? Edge gone from OAIB? (R3, R9) | | | |
| Q9 | OABD node of the deleted activity gone after delete+save, or only after a refresh? (R9 "re-synchronize on save") | | | whether the template must refresh breakdown diagrams |
| Q10 | Diagrams rooted at the deleted element deleted by Capella? (committed OABD for Exibir, Spike OABD for Monitorar, Spike OEBD for Veículo) (R9, Resolved 4) | | | `diagrams_deleted` computation |
| Q11 | Diagrams that showed it (OAIB, OEB, OCB): node and edges gone, no dangling views, still refresh and export? (R9, hypothesis b) | | | |
| Q12 | uuid absent from `.capella` and `.aird` after save? (hypothesis a) | | | integration test assertion |
| Q13 | Does `delete_ok` + in-probe "removed" match the file-level diff? (R7: result computed in Capella is trustworthy) | | | R7 implementation |
| Q14 | Root Operational Activity: does Capella itself refuse? (R2, informational) | | | |
| Q15 | Negative controls: `ecoreutil_delete` leaves N dangling refs. Do Sirius' own triggers hide them in diagrams? Do `emf_delete_command`/`sirius_util_delete` differ from Capella's command? | | | confirms R5's ban, or shows a simpler path |
| Q16 | Attach mode: dialog? GUI updated live? Undo offered? Model left dirty, not saved? Files correct after the user saves? Go/no-go for `_dispatch()` vs spawn-only (R6, Resolved 5) | | | `_dispatch` or `_run_script` |
| Q17 | Runtime per delete call (spawn), for the tool's timeout | | | |

## Known limits of the kit

- Class names, the ExecutionManager lookup and the constructors are
  unverified guesses. Discovery corrects names, and `--candidates` overrides
  the rest without code changes.
- The verify pass refreshes **every** diagram. That's the only way to see a
  breakdown re-sync, but it also means "after refresh" is not exactly "what
  the user sees right after the tool returns". The "after delete" columns
  are that.
- EMF `Diagnostician` checks structural constraints only (e.g. references
  to objects not in a resource), not Capella's validation rules.
- The seed relies on `create_element`/`create_*diagram`. If one fails, the
  related checks say "seed_missing" or have nothing to compare. See
  `seed_info.json`.
- Attach observations are your answers to the prompts. The kit can't see
  the GUI.
