# capcli

A small command line for reading and editing [Capella](https://mbse-capella.org/)
models, built on [py-capellambse](https://github.com/DSD-DBS/py-capellambse).
It is designed for coding agents (Claude Code, OpenCode, …) that work through a
shell. Every command prints JSON, writes are guarded, and Capella doesn't need
to be running.

## Why not plain capellambse scripts?

Agents can write capellambse scripts, but a few traps come up again and again.
capcli handles them for you:

| Trap | What capcli does |
|---|---|
| `create()` can pick the wrong metaclass (e.g. a `LogicalFunction` under an `OperationalActivity`) | Always passes the right type per layer (OA/SA/LA/PA) |
| Removing an element leaves `target=""` / dangling references | `delete` refuses while the element is referenced. `--cascade` also removes exchanges, allocations, Parts and orphaned ports |
| Functional/component exchanges need ports and the right owner | Ports are created and the exchange is placed in the common parent |
| `PhysicalComponent.components` is a computed list | Uses `owned_components` |
| capellambse accepts sub-systems in SA and actors inside the system, both against Arcadia | `create component` refuses them, and `check` reports existing ones under `structure` |
| capellambse can't edit chains through `involved_*`, and `capability.involved_chains.append()` rejects chains | `chain` commands create the involvements directly and keep links and functions consistent |
| Realizations written with only `targetElement` (Capella always writes `sourceElement` too) | `realize` writes both ends. `check` reports incomplete links and `check --fix` repairs them |
| Partial writes after an error | `batch` applies a list of steps all-or-nothing |
| Hard to tell if the model is still sound | `check` (reference integrity) and `validate` (capellambse rules), plus an `issues` list in `chain show` and `capability show` |

## Install

```bash
pip install -e .          # or: pipx install git+https://github.com/<you>/CapellaPyCLI
capcli --help
```

capellambse 0.8.x is required (tested here on a Capella 7.0 model). Check
[its docs](https://dsd-dbs.github.io/py-capellambse/) for your version.

## Use with an agent

There are two ways to give an agent the rules and the command reference. Use
either one, or both.

**Option A: `AGENTS.md`.** The agent loads it in every session.

1. Copy [`templates/AGENTS.md`](templates/AGENTS.md) into the root of your
   model repository and adjust the marked sections (model path, allowed layers,
   naming rules). For Claude Code, add `@AGENTS.md` to `CLAUDE.md`.

**Option B: an Agent Skill.** The agent loads it only when the task involves the model.

1. Copy the folder [`templates/skills/capella-model/`](templates/skills/capella-model)
   into your model repository:
   - OpenCode: `.opencode/skills/capella-model/SKILL.md`. OpenCode also reads
     `.claude/skills/` and `.agents/skills/`, and you can put it in
     `~/.config/opencode/skills/` to use it in every project.
   - Claude Code: `.claude/skills/capella-model/SKILL.md`.
2. Edit the "Project settings" table (model path, allowed layers, naming rules).
   Keep the folder name and the `name:` field equal (`capella-model`).

A skill keeps the context small when most tasks don't touch the model. But the
agent only follows its rules once it decides to load the skill. If you need the
"never edit model files directly" rule to apply always, use `AGENTS.md`. You
can also keep a one-line `AGENTS.md` that points to the skill.

In both cases:

- Keep the model in git. Have the agent work on a branch with Capella closed.
- Review with `git diff`, then open the model in Capella to place new elements on diagrams.

`tests/test_docs.py` checks that both templates document every command and
batch op, and that their batch examples run.

## Commands

```text
read:    info · list · search · show · tree · diagrams list|render
write:   create function|component|function-exchange|component-exchange
         allocate · unallocate · realize · set · delete · batch
chains:  chain list|show|create|add|remove|involve
capab.:  capability list|show|create|involve|uninvolve|include|extend|generalize
         mission create|show|exploit|involve · realize · unrealize
verify:  check · validate
global:  --model PATH (or $CAPELLA_MODEL) · --dry-run
```

See `templates/AGENTS.md` for a full cheat sheet, or `capcli <command> --help`.

## What you can read and change today

Capella models are organised in four layers. capcli calls them `oa`
(Operational Analysis), `sa` (System Analysis), `la` (Logical Architecture)
and `pa` (Physical Architecture). Everything below was checked against the
Capella 7.0 test model in `tests/data/model`.

Legend:

- ✅ supported
- 🟡 partly supported (see the notes)
- 👁 read only
- ❌ not possible with capcli

### Reading

Reading is broad. Every element in the model can be found and inspected, even
the kinds capcli can't create or modify.

| What | How | Notes |
|---|---|---|
| Model overview | `capcli info` | Capella version, number of diagrams, element counts per layer |
| Element lists per layer | `capcli list <layer> <kind>` | See the table of kinds below. `--name` filters by name |
| Any element by name or type | `capcli search <text> [--type <Metaclass>] [--layer <layer>]` | Any metaclass works, e.g. `Scenario`, `StateMachine`, `State`, `Mode`, `ExchangeItem`, `Class`, `Constraint`, `PropertyValueGroup`, `Part`. Parts and ports only show up when you ask for them with `--type` |
| Element details | `capcli show <uuid>` | Name, description, parent, sub-functions or sub-components, ports, incoming and outgoing exchanges with the functions at each end, allocation, realizations, chains involved in, diagrams it appears on |
| Any attribute | `capcli show <uuid> --attr <name>` | Any capellambse attribute, e.g. scenario `messages`, class `properties`, exchange item `elements`, property `value`, capability `involved_components`. If the attribute is named `uuid`, `type` or `layer`, it is returned as `attr_uuid`, `attr_type` or `attr_layer` |
| Breakdown | `capcli tree <uuid> --depth N` | Functions with the component each is allocated to, or components with their sub-components |
| Functional chains | `capcli chain list / show` | Ordered steps, entry and exit functions, and integrity issues |
| Diagrams | `capcli diagrams list / render` | Lists diagrams (name, type, target) and renders one to SVG, as saved in the `.aird` |
| Capabilities and missions | `capcli capability list / show`, `capcli mission show` | Involved components, actors or entities, functions and chains; realizations up and down; include, extend and generalize relations in both directions; exploiting missions (SA); scenarios; and an `issues` list |
| Model health | `capcli check`, `capcli validate` | Arcadia structure violations, broken, empty or incomplete references (`check --fix` repairs missing realization sources), and capellambse's validation rules |

Kinds available in `capcli list <layer> <kind>`:

| Kind | oa | sa | la | pa |
|---|:-:|:-:|:-:|:-:|
| `functions` (activities in OA), `function-exchanges` | ✅ | ✅ | ✅ | ✅ |
| `activities`, `activity-exchanges`, `entities`, `entity-exchanges`, `operational-processes`, `processes` | ✅ | | | |
| `components`, `component-exchanges` | | ✅ | ✅ | ✅ |
| `actors` | ✅ | ✅ | ✅ | ✅ |
| `actor-exchanges` | | ✅ | ✅ | |
| `capabilities` | ✅ | ✅ | ✅ | ✅ |
| `missions`, `capability-exploitations` | | ✅ | | |
| `functional-chains` | ✅¹ | ✅ | ✅ | ✅ |
| `physical-links`, `physical-paths`, `physical-exchanges` | | | | ✅ |
| `requirements`, `requirement-types`, `relation-types` | ✅ | ✅ | ✅ | ✅ |
| `classes`, `collections`, `complex-values`, `enumerations`, `unions`, `interfaces`, `module-types` | ✅ | ✅ | ✅ | ✅ |

¹ In OA, use `operational-processes` or `capcli chain list oa`, because
`functional-chains` comes back empty.

### Creating and modifying

| Element or relation | Create | Modify | Delete | Notes |
|---|:-:|:-:|:-:|---|
| Functions (activities in OA) | ✅ | ✅ | ✅ | All four layers. Created below a parent function, with the right metaclass for the layer |
| Components (entities in OA), actors | ✅ | ✅ | ✅ | `--actor`. `--nature node\|behavior` in PA. The Part that Capella needs is created automatically. Arcadia rules are enforced: no components inside the SA System (it's a black box; decompose in LA), and actors only in the Structure package (`<layer>:structure`). `is_actor` can't be toggled with `set` |
| Functional exchanges | ✅ | ✅ | ✅ | Ports are created automatically. In OA, activities are connected directly |
| Component exchanges (communication means in OA) | ✅ | ✅ | ✅ | `--kind flow\|delegation\|assembly`. Component ports are created automatically |
| Function → component allocation | ✅ | ✅ | ✅ | `allocate`, `unallocate`, `allocate --move` |
| Functional exchange → component exchange allocation | ✅ | | ✅ | `allocate`, `unallocate` |
| Realization (function, component, chain or capability → the layer above) | ✅ | | ✅ | `realize`, `unrealize`. Both ends of the link are written, as Capella does |
| Functional chains / operational processes | ✅ | ✅ | ✅ | `chain create / add / remove`, with `--path` |
| Capabilities (OperationalCapability, Capability, CapabilityRealization) | ✅ | ✅ | ✅ | `capability create` picks the metaclass for the layer. Name and description with `set` |
| Involvement in a capability: components, actors, entities, functions, chains | ✅ | | ✅ | `capability involve` / `uninvolve`. `chain involve` still works too |
| Capability include / extend / generalize | ✅ | | ✅ | `capability include`, `extend` or `generalize`, each with `--remove` |
| Missions (SA): exploited capabilities, involved actors | ✅ | | ✅ | `mission create`, `mission exploit`, `mission involve` (`--remove` to undo) |
| Text and simple attributes of any element | | ✅ | | `set`: name, description, summary, review, sid, booleans, numbers, and enumerations such as function `kind`, PA `nature` or exchange item `type`. A wrong enumeration value is rejected with the list of allowed values |
| Existing requirement text | | ✅ | | `set <req> text="<p>…</p>"` |
| Existing property values (PVMT) | | ✅ | | `set <property value> value=…` |
| Any element inside a layer | | | ✅ | `delete` refuses while the element is still referenced. `--cascade` also removes the exchanges, allocations, realizations, involvements, Parts and orphaned ports that depend on it. Packages and layer roots are never deleted |
| Several changes at once | ✅ | ✅ | ✅ | `batch`: all or nothing, and later steps can refer to elements created earlier (`"as"` / `"$name"`) |

### Not possible today

These elements can all be **read** with `search`, `show` and `show --attr`.
Changing them needs Capella, or a reviewed capellambse script.

| Area | What is missing |
|---|---|
| Structure | Creating packages. Moving an element to another parent. Reordering elements |
| Capabilities | Capability packages. Pre- and postconditions (see Constraints) |
| Scenarios | Scenarios, instance roles, sequence messages, fragments |
| Modes and states | State machines, regions, states, modes, transitions. "Available in states" on functions and chains |
| Data model | Classes, properties, data types, enumerations, unions, collections, exchange items. Assigning exchange items to exchanges, ports or chain links. Existing items' simple attributes can still be changed with `set` |
| Interfaces and ports | Interfaces, interface allocation and implementation. Creating or allocating ports on their own: ports only come into being with an exchange |
| Physical architecture | Physical links, physical paths, physical ports, deploying behaviour components on node components |
| Functional chains | Control nodes and sequence links of complex chains (AND/OR/ITERATE). They are kept but not editable, and `chain show` leaves them out. Exchange contexts and exchanged items on chain links |
| Requirements | Creating requirements, or linking them to model elements. Existing text and attributes can be changed with `set` |
| Property values (PVMT) | Creating property values or groups, or applying them to elements. Existing values can be changed with `set` |
| Constraints | Constraints, preconditions and postconditions on capabilities and chains |
| Status | `status` and `progress_status` can't be set |
| Diagrams | Creating diagrams, or adding new elements to them: new elements exist in the model but aren't drawn. Deleted elements stay on diagrams until you clean them up in Capella (`delete` warns you). `diagrams render` shows the layout as saved |

### Other limits

- **Capella must be closed while capcli writes.** Otherwise Capella overwrites
  the changes, or the two versions conflict. There is no locking.
- **Tested on Capella 7.0 only**, with capellambse 0.8.x. Other versions that
  capellambse supports should work, but check the capellambse docs and test on
  a copy first.
- **Library projects** (shared models referenced by other models) haven't been tested.
- **Use the command line only.** Don't edit the model's XML directly.
  `capcli check` finds broken references, but can't repair them.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Tests run against capellambse's Capella 7.0 test model, vendored in
`tests/data/model` (© DB InfraGO AG, Apache-2.0, see the `.license` files).
