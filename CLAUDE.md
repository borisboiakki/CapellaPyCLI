# CLAUDE.md: working on capcli

This file is for people and agents who **develop capcli**. It is not for
agents that *use* capcli on a model: those read `templates/AGENTS.md` or the
skill in `templates/skills/capella-model/`, which get copied into the model's
repository. Keep the two audiences apart.

## What capcli is

capcli is a command line for reading and editing [Capella](https://mbse-capella.org/)
models (`.aird` / `.capella` / `.afm` files), built on
[py-capellambse](https://github.com/DSD-DBS/py-capellambse) 0.8.x. Its users
are coding agents (Claude Code, OpenCode, …) that work through a shell, so:

- every command prints **one JSON document** on stdout;
- writes are guarded: explicit metaclasses, reference checks, Arcadia rules,
  `--dry-run`, and atomic `batch`;
- Capella doesn't need to be running. In fact it must be **closed** while
  capcli writes.

The model files are XMI: elements are linked by UUID references (`#uuid`
attributes). A naive edit leaves dangling or half-written links that Capella
then rejects or shows wrongly. capcli's whole value is producing XML that
matches what Capella itself writes.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q                       # ~2 min, all tests must pass
.venv/bin/pip install pyflakes && .venv/bin/python -m pyflakes src tests  # no linter config; keep it clean
.venv/bin/capcli -m tests/data/model info # try it on the test model
```

Never run a write command against `tests/data/model` directly, because it is
the test fixture. Copy it first:
`cp -r tests/data/model /tmp/m && capcli -m /tmp/m …`.

## Repository layout

```text
src/capcli/
  cli.py           click commands, JSON output, error handling, --dry-run, batch entry
  model.py         loading, element lookup/shortcuts, layer + metaclass maps,
                   JSON serialization (brief/detail), shared predicates
  ops.py           core write operations (create/allocate/realize/set/delete),
                   XML-level helpers (iter_refs, delete cascade, check),
                   Arcadia structure rules, batch runner and the OPS registry
  chains.py        functional chains / operational processes
  capabilities.py  capabilities (OA/SA/LA/PA) and missions (SA)
  status.py        progress status (ProgressStatus values)
  data.py          data model: classes, properties, enumerations, exchange items
  modes.py         modes and states: state machines, regions, states, transitions
  physical.py      physical architecture: ports, links, paths, deployment
  structure.py     packages, moving elements, repair structure
  interfaces.py    interfaces: exchange items, provide/require, allocation
templates/
  AGENTS.md                         agent instructions for a *model* repository
  skills/capella-model/SKILL.md     the same as an Agent Skill (OpenCode, Claude Code)
tests/
  conftest.py      `model` (fresh copy of the test model) and `run` fixtures
  test_cli.py      core commands, and functional chains (at the end)
  test_capabilities.py  capabilities, missions, realization sources
  test_structure.py     Arcadia structure rules
  test_status.py        progress status
  test_data.py          data model
  test_modes.py         modes and states
  test_physical.py      physical architecture
  test_move.py          packages, move, repair structure
  test_interfaces.py    interfaces
  test_docs.py     keeps the templates in sync with the CLI (see below)
  data/model/      capellambse's Capella 7.0 test model (Apache-2.0, DB InfraGO AG)
```

## Architecture

### Dependency direction

```text
model.py          ← imports only capellambse
chains.py         ← imports model
capabilities.py   ← imports model
status.py         ← imports model
data.py           ← imports model
modes.py          ← imports model
physical.py       ← imports model
structure.py      ← imports model
interfaces.py     ← imports model
ops.py            ← imports model and every feature module (merges their OPS)
cli.py            ← imports everything
```

`model.py` depends on nothing in the package. The feature modules
(`chains.py`, `capabilities.py`, `status.py`, `data.py`, `modes.py`,
`physical.py`, `structure.py`, `interfaces.py`) import only
from `model.py`. Helpers they share, such as `status_name` / `with_status` and
`add_xml_child` / `datavalue_alias` (creating elements capellambse can't),
`ancestors` / `common_owner` (where an exchange or link belongs),
live in `model.py`. `ops.py` imports both and
merges their `OPS` dicts into its registry. Keep it that way: a feature
module importing `ops` creates an import cycle (this happened once, and is
why the shared helpers live in `model.py`).

### Request flow

1. `cli()` builds a `Ctx` holding the model path (`--model`, then
   `$CAPELLA_MODEL`, then the only `.aird` under the current directory) and the
   `--dry-run` flag. The model is loaded lazily on first access.
2. A **read** command (`@click.pass_obj` + `@handled`) resolves elements and
   calls `emit()`.
3. A **write** command uses `@write_command`. The function gets the loaded
   model plus the CLI arguments, calls an operation, and returns a dict.
   The decorator then saves the model (unless `--dry-run`) and emits
   `{...result, "saved": bool}`.
4. Errors: operations raise `CapError("actionable message")`. `@handled` turns
   it, or any unexpected exception, into `{"error": "..."}` with exit code 1.
   Nothing is saved after an error.

Exit codes: **0** ok, **1** error, **2** `check` found problems.

### Operations and batch

Every write is a plain function `op(model, **kwargs) -> dict`. It mutates the
in-memory model and **never saves**. It is registered under a kebab-case name
in an `OPS` dict (`ops.OPS`, `chains.OPS`, `capabilities.OPS`).

`capcli batch` (`ops.run_batch`) runs a JSON list of steps against one loaded
model and saves only if every step succeeds, so it is all-or-nothing.
`"as": "x"` stores the UUID from a step's `result["created"]`, and `"$x"` in
later steps (also inside lists and dicts) is replaced with it. Batch arguments
are the op's keyword names, with `-` normalized to `_`. There are a few
explicit aliases in `run_batch`: `from` → `from_`, `class` → `cls`
(`add-property`), and `layer` → `layer_name` for any op whose function
has a `layer_name` parameter.

A create op must return `{"created": brief(obj), ...}` so it works with `"as"`.

### Element references

`model.resolve()` accepts a UUID or a shortcut:
`<layer>:root-function` (`oa:root-activity`), `<layer>:root-component`
(`oa:root-entity`, which is the OA entity *package*), `<layer>:structure`
(the Structure package, where actors live), and the root packages
`<layer>:functions`, `:capabilities`, `:data`, `:interfaces`.

### JSON shapes (keep them stable, because agents parse them)

- `brief(obj)` → `{"uuid", "type" (metaclass), "name"}`. It is used everywhere.
- `detail(obj)` (`show`) adds `layer`, description, and the relations in
  `SHOW_ATTRS`, plus computed keys: `allocated_to`, `incoming_exchanges`,
  `outgoing_exchanges`, `chains`, `diagrams`. `show --attr X` returns any
  attribute. `uuid` / `type` / `layer` come back as `attr_uuid` and so on, so
  they don't clobber the identity keys.
- `status` appears in every show view when the element has one: `detail`,
  `show_chain`, `show_capability` and `show_mission` all use
  `model.with_status`. Add it to any new show view.
- `show` delegates to `chains.show_chain`, `capabilities.show_capability` or
  `show_mission` for those kinds. These return an `issues` list (strings)
  that agents are told to keep empty.

### Low-level XML work (ops.py)

capellambse's high-level API is used for creating things. Some jobs go to the
lxml tree directly (`obj._element`, `model._loader`):

- `iter_refs(model)`: every `#id` reference in semantic files (or visual ones,
  with `visual=True`).
- `delete`: computes everything that references the element's subtree.
  Without `--cascade`, it refuses. With `--cascade`, it removes referrers whose
  metaclass matches `_CASCADABLE` (exchanges, allocations, realizations,
  involvements, Parts, ports, include/extend/generalization/exploitation),
  repeated until nothing new is found. Ports left without exchanges are
  removed too. References in `_DETACHABLE_ATTRS` (exchange items carried by
  exchanges and ports, `availableInStates`, a transition's `effect` and
  `triggers`) are *detached* under `--cascade`: the ID is removed from the
  list and the referrer is kept. `_ALWAYS_DETACH_ATTRS` (the state caches
  `involvedStates` / `referencedStates`) are updated even without
  `--cascade`. Interfaces provided/required by ports (`providedInterfaces`,
  `requiredInterfaces`) are detachable too. A `PhysicalPathInvolvement` referrer means a link or node
  inside a physical path: with `--cascade` the whole path is deleted (a path
  missing a hop is meaningless), otherwise the path blocks the delete. Any other referrer blocks the delete. Diagram references are
  only reported as a warning.
- Removing an element: `model._loader.idcache_remove(el)` and then
  `el.getparent().remove(el)`. Always do both.
- `check`: dangling refs, empty required refs, realization links without
  `sourceElement`, stale state caches, interface implementations written by
  capellambse with the wrong attribute (`--fix` repairs all three), and
  Arcadia structure violations.

## capellambse 0.8.1 traps (all found the hard way)

Check these again when upgrading capellambse.

| Trap | What capcli does |
|---|---|
| `create()` infers the wrong metaclass, e.g. a `LogicalFunction` below an `OperationalActivity` | Always pass the metaclass: `FUNCTION_TYPE`, `COMPONENT_TYPE`, `CAPABILITY_TYPE` per layer |
| Realization links get only `targetElement`; Capella always writes `sourceElement` too | `ops._complete_trace_sources()` after every realize; `check --fix` repairs old links |
| Removing via capellambse can leave `target=""` or dangling links | `delete` works on the XML with a reference scan (see above) |
| `PhysicalComponent.components` is computed (owned + deployed); `create` fails ("List is not coupled") | Use `owned_components` in PA |
| `capability.involved_chains.append(chain)` rejects chains (it expects functions) | Create `FunctionalChainAbstractCapabilityInvolvement` directly |
| PA `CapabilityRealization.involved_components` reads back empty | Read `capability_realization_involvements[*].involved` |
| `included_by` / `extended_by` / `generalized_by` return the *link* objects | Use `link.parent` to get the capability |
| `FunctionalChain.involved_*` are read-only properties | Create `FunctionalChainInvolvementFunction` / `…Link` directly |
| `sa.mission_pkg` is a list | Use `[0]` |
| OA: `functional-chains` is empty; processes are `all_operational_processes` | `chains.all_chains()` |
| OA `root_entity` is deprecated; entities belong in `entity_pkg` | `oa:root-entity` resolves to `entity_pkg` |
| OA activities have no ports; exchanges connect activities directly. OA entity links are `CommunicationMean` | Handled in `create_function_exchange` / `create_component_exchange` |
| Requirements have no `name` attribute (they use `ReqIFLongName`) | `search` falls back to the `name` accessor |
| `InterfaceAllocation` is marked abstract, so `allocated_interfaces.append` fails | `interfaces.allocate_interface` writes it with `add_xml_child` (`ownedInterfaceAllocations`, `targetElement` = interface, `sourceElement` = allocator, as capellambse's own definition says). No Capella-written example in the test model: verify in Capella |
| `Component.implemented_interfaces` writes `implementedInterfaces` (plural); Capella uses `implementedInterface` and can't read the plural | `interfaces.provide` writes the singular at XML level; `check` reports the plural, `--fix` renames it |
| `status` accepts *any* `EnumerationPropertyLiteral`, including PVMT values; `status = None` raises | `status.py` only accepts literals of the project's `ProgressStatus` type, and clears with `del obj.status` |
| `progress_status` has no setter, but it is only the name of the `status` literal | Setting `status` sets it. `set progress_status=…` is treated as `status` |
| Properties and exchange item elements get no `ownedMinCard`/`ownedMaxCard`, and `min_card = …` raises | `data._set_cards` writes `LiteralNumericValue` children at XML level (`loader.new_uuid` + `idcache_index` *inside* the `with`, or it raises KeyError) |
| `EnumerationLiteral` misses `abstractType` (back-reference to its enumeration); `ExchangeItemElement` misses `direction="UNSET" composite="true"` | Set explicitly in `data.py` |
| `DataPkg.enumerations` is a filter, but creating through it works; `ExchangeItemElement.abstract_type` is deprecated | Create enumerations with `data_types.create("Enumeration")`; use `type` |
| Exchange items: functional exchanges use `exchanged_items`, function ports `exchange_items` (`incoming`/`outgoingExchangeItems` in XML), component exchanges `convoyed_informations` (`allocated_exchange_items` is deprecated); component ports carry interfaces, not items | `data.CARRIER_ATTR` |
| Regions don't get `involvedStates`, states don't get `referencedStates` or their own sub-region "region" (Capella always has all three) | `modes.sync_caches()` after every state change; `add_state` creates the region; `check --fix` rebuilds caches |
| A new `Constraint` has no `ownedSpecification`, and `specification[...] = …` raises (it is `None`) | `modes._set_guard` writes the `OpaqueExpression` (`bodies` + `languages`) with `model.add_xml_child` |
| `StateTransition.effects` is deprecated; the accessor is `effect` (a list) | Use `effect` |
| Deployment: capellambse also offers `InstanceDeploymentLink`, but Capella uses `PartDeploymentLink`, owned by the host's Part (`location` = host Part, `deployedElement` = deployed Part) | `physical.deploy` |
| `PhysicalPath.involved_items.append()` writes involvements without the `nextInvolvements` chain that orders them | `physical.create_path` writes it; `_path_hops` reads the order from it |
| `PhysicalComponent.deployed_components` is computed; deployment lives on Parts | Resolve the component's Part (`physical._part`) |
| Appending an element to another containment list moves it, but a component's Part stays in the old parent | `structure._move_part` moves the Part (tag `ownedFeatures` in a component, `ownedParts` in a package) |
| After a move, exchanges/links keep their old owner, which may no longer be the common parent of their ends | `structure._rehome_links` (same rule as creation: `model.common_owner`) |
| `PhysicalComponent` keeps sub-packages in `component_pkgs`; every other component/function/package uses `packages` | `structure._pkg_list` |
| `obj.name` on unnamed link elements, and some deprecated accessors, raise `FutureWarning` | Warnings are silenced in `main()`. Wrap fallbacks in `warnings.catch_warnings()` |
| capellambse can't create or lay out diagrams | Out of scope. New elements aren't drawn |
| capellambse allows any containment, e.g. sub-systems in SA | Arcadia rules enforced in capcli (next section) |

## Invariants: don't break these

1. **Explicit metaclasses** on every create.
2. **Same layer** for exchanges, allocations, involvements and relations
   (`model.same_layer`). Cross-layer links are only realizations, from a layer
   to the one directly above (`oa ← sa ← la ← pa`). Data is the exception:
   a type or exchange item may come from the same layer or any layer above
   (an LA property typed by SA's `Integer`), never from a layer below.
3. **Modes vs states**: a region holds modes or states, never both, and at
   most one initial pseudo-state (`modes.add_state`).
4. **Physical architecture**: physical ports and links only on node
   components; a node is never deployed on a behaviour component
   (`physical.py`).
5. **Arcadia structure** (`ops._check_structure_rules`,
   `ops.structure_violations`):
   - SA is a black box: the System is the only non-actor SA component;
   - actors (SA/LA/PA) live in the Structure package, never inside a component;
   - `is_actor` can't be toggled with `set`;
   - `move` applies the same rules, and moves stay within a layer.
6. **Every realization has both ends** (`targetElement` and `sourceElement`).
7. **delete never leaves a dangling reference.** `capcli check` must stay
   `ok` after every write in every test.
8. **Ops never save.** Only `write_command`, `batch` and `check --fix` save,
   and never after an error.
9. **Errors say what to do instead**: the command, the shortcut, the
   allowed values.
10. **JSON keys are an API.** Add keys, but don't rename or remove them.

## How to add a feature (checklist)

1. **Probe capellambse first**, on a copy of the test model. Write a scratch
   script (pattern below) that creates the elements, saves, reloads and runs
   `capcli check`.
2. **Compare with Capella's XML.** Find a Capella-written example of the same
   construct in `tests/data/model/*.capella` (`grep -n ':MetaclassName"'`) and
   diff the attributes capellambse wrote against it. Missing attributes, like
   `sourceElement`, are bugs to compensate for.
3. **Implement the op** in the right module: `ops.py` for core elements, or a
   new module importing only `model.py`. Use `resolve`, `same_layer`, explicit
   metaclasses and idempotent behaviour (`{"unchanged": True, "reason": …}`
   when nothing to do). Add `--remove` or an inverse command for every link
   you can create.
4. **Register it** in the module's `OPS`, and in `ops.py` if it's a new
   module. Add batch argument aliases in `run_batch` only if unavoidable.
5. **Add the CLI command** with `@write_command`, or `@click.pass_obj` +
   `@handled` for reads. Put it in a click group when there are several
   related commands.
6. **Make it visible**: `show` / `detail` / a dedicated `show_*` with an
   `issues` list for anything with consistency rules.
7. **Delete and check**: if the new link elements reference other elements,
   make sure `_CASCADABLE` covers them (otherwise deleting the target is
   blocked) and that `check` stays `ok`.
8. **Tests**: one test module per feature, using the `run` fixture. Cover
   create, read back after reload, idempotence, error paths, the
   cross-layer/structure rules, delete with and without `--cascade`, and
   `check` ok at the end. Compare written XML with lxml when the shape
   matters (see `test_capabilities.py`).
9. **Docs** (`test_docs.py` fails until you do):
   - every command and batch op must appear in **both**
     `templates/AGENTS.md` and `templates/skills/capella-model/SKILL.md`;
   - the batch examples in both are executed by the tests;
   - update the README tables: "Reading", "Creating and modifying",
     "Not possible today", and the trap table at the top.
10. **Run** `pytest -q` and `pyflakes src tests`. Before a PR, run one batch
    end to end on a scratch copy and inspect `git diff` of the `.capella` file.

Probe script pattern:

```python
import capellambse, warnings, sys
warnings.simplefilter("ignore")
m = capellambse.MelodyModel(sys.argv[1])          # path to a *copy* of the .aird
x = m.la.root_function.functions.create("LogicalFunction", name="probe")
print(type(type(x).functions).__name__)          # Containment / Allocation / property / Backref
m.save()
print(capellambse.MelodyModel(sys.argv[1]).by_uuid(x.uuid).name)
```

The descriptor type tells you what you can do with an attribute.
`Containment` supports `.create(...)`. `Allocation` supports
`.append(obj)`, which creates a link element. A plain `property` is
computed and read-only. A `Backref` is read-only.

## Testing notes

- `conftest.py`: `model` copies `tests/data/model` into `tmp_path`.
  `run(*args, input=None, ok=True)` invokes the CLI on it and returns the
  parsed JSON, or `(json, exit_code)` with `ok=False`.
- Every `run` call reloads the model from disk, so assertions after a write
  also prove the change survived a save and reload.
- Useful test-model facts:
  - SA "Capability" = `9390b7d5-…` (it includes itself, which is test data, not a bug);
  - SA System = `230c4621-…`;
  - OA "Stay alive" = `83d1334f-…`;
  - LA root component "Hogwarts" = `0d2edb8f-…`;
  - SA "Test Chain" = `dfc4341d-…`;
  - 8 requirements, some unnamed.
- When you add a guard test, check that it actually fails: temporarily break
  the doc or code and watch the test go red.
- Nothing has been opened in Capella by the tests. The XML comparison in
  step 2 above is the only check against Capella's real behaviour. When
  possible, open a sample result in Capella before releasing a feature.

## Conventions

- Python ≥ 3.10, `from __future__ import annotations`, type hints on public
  functions, short docstrings that explain *why*. Match the style of the
  surrounding code.
- Element arguments are named after their role (`element`, `to`, `parent`,
  `capability`, `chain`, …). Lists of elements are `elements`.
- Comments explain capellambse or Capella quirks, with what goes wrong
  otherwise.
- Git:
  - work on a branch and open a PR to `main`;
  - PRs are merged with a merge commit;
  - the repository has no CI, so run the tests locally before pushing;
  - don't put AI model names in commits, PRs or code.

## Known technical debt

- `CHAIN_TYPES` is defined three times (`chains`, `capabilities`, `ops`), and
  `_remove(model, link)` twice (`chains`, `capabilities`). Both belong in
  `model.py`.
- `ops.py` mixes core ops and XML utilities. `iter_refs` / `delete` / `check`
  could move to their own `integrity.py`.
- `delete` and `check` scan the whole model on every call, which is fine for
  the test model. Watch performance on large models (thousands of elements).
- Each CLI call reloads the model, which takes a few seconds on big models.
  `batch` is the workaround. A long-running server mode could be added later.
- `capability list` computes the full `show` for every capability to count
  issues.
- The relation batch ops (`capability-include` etc.) are lambdas in
  `capabilities.OPS`.
- Only Capella 7.0 is tested. Library projects (REC/RPL, referenced
  libraries) are untested.

## Roadmap (agreed priorities)

capellambse can already write all of the following (each was probed: save,
reload, `check` ok). Only capcli commands are missing. Roughly in order:

1. ~~`status`~~: done (`capcli status`).
2. **Requirements** (skipped for now, at the user's request): create in a `CapellaModule`, and link or unlink to
   elements. In a probe, `r.relations.create("CapellaOutgoingRelation", target=fn)`
   linked the requirement to the function (it appeared in `fn.requirements`),
   but capellambse wrote a `CapellaIncomingRelation`. Check the
   direction against Capella-written relations before relying on it.
3. ~~Data model and exchange items~~: done (`capcli data`). Still missing:
   unions, collections, physical quantities, creating basic types, class
   generalization, exchange items on chain links.
4. ~~Modes and states~~: done (`capcli mode`). Still missing: entry/exit/do
   activities, history and entry/exit pseudo-states, state realizations.
5. ~~Physical architecture~~: done (`capcli pa`). Still missing: physical
   link categories, physical path realizations.
6. **Scenarios** and **complex chains** (sequence nodes and links,
   exchange context, exchanged items). These are the hardest to get valid for
   Capella, because messages and nodes have ordering rules.
7. ~~Interface allocation~~: done (`capcli interface`), together with
   interface creation, exchange items, provide/require.
8. ~~Structure~~: done (`capcli package create`, `move`, `repair structure`).
   Still missing: reordering elements, an automatic fix for SA sub-systems.

Capability pre- and postconditions (constraints) are also missing.

Out of reach with capellambse: creating or laying out diagrams.
