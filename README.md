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
| Removing an element leaves `target=""` / dangling references | `delete` refuses while the element is referenced. `--cascade` also removes the links that depend on it (exchanges, allocations, Parts, orphaned ports), never the elements that merely point to it |
| Functional/component exchanges need ports and the right owner | Ports are created and the exchange is placed in the common parent |
| `PhysicalComponent.components` is a computed list | Uses `owned_components` |
| capellambse accepts sub-systems in SA and actors inside the system, both against Arcadia | `create component` refuses them, and `check` reports existing ones under `structure` |
| capellambse can't edit chains through `involved_*`, and `capability.involved_chains.append()` rejects chains | `chain` commands create the involvements directly and keep links and functions consistent |
| Realizations written with only `targetElement` (Capella always writes `sourceElement` too) | `realize` writes both ends. `check` reports incomplete links and `check --fix` repairs them |
| `status` accepts any enumeration literal (even PVMT values), and `status = None` raises | `status` only accepts the project's ProgressStatus values, and `NOT_SET` clears it |
| Data elements are written without multiplicities, literal back-references or Capella's default element flags, and multiplicities can't be created through the API | `data` commands write them the way Capella does |
| Deleting an exchange item would cascade to every exchange that carries it | `delete --cascade` *detaches* the item from exchanges and ports and keeps them |
| State machines miss Capella's caches (`involvedStates`, `referencedStates`) and per-state regions; guards are created without their text | `mode` commands maintain them, and `check --fix` rebuilds stale caches |
| capellambse offers an `InstanceDeploymentLink` for deployment and writes physical paths without their hop order (`nextInvolvements`) | `pa deploy` writes Capella's `PartDeploymentLink`; `pa path` writes the hop chain |
| Moving a component leaves its Part in the old parent, and exchanges keep an owner that may no longer be the common parent of their ends | `move` moves the Part and re-homes exchanges and physical links |
| Interface implementations are written with `implementedInterfaces` (Capella uses `implementedInterface`), and interface allocations can't be created at all | `interface provide` / `interface allocate` write Capella's form; `check --fix` repairs implementations written by plain capellambse |
| Boolean types are created without their True/False literals, collections without multiplicity, and constraints (guards, pre/postconditions) without their text | `data type`, `data collection`, `mode transition --guard` and `… condition` write them as Capella does |
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
2. Nothing else to set: at the start of a session the agent asks which model
   (`.aird` file) to use and which Capella perspectives (OA, SA, LA, PA) it
   may change, unless `CAPELLA_MODEL` or your `AGENTS.md` already says so.
   Adjust the naming conventions if needed, and keep the folder name and the
   `name:` field equal (`capella-model`).

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
chains:  chain list|show|create|add|remove|involve|items|condition
capab.:  capability list|show|create|involve|uninvolve|include|extend|generalize|condition
         mission create|show|exploit|involve · realize · unrealize
status:  status values|set|list
iface:   interface list|show|create|items|provide|require|allocate
struct:  package create · move · reorder · repair structure
pa:      pa list|show · pa port · pa link · pa path · pa deploy · pa category|categorize
modes:   mode list|show · mode machine create · mode add · mode transition · mode available
         mode activity
data:    data types|show · data type · data class create · data union · data collection
         data property add · data enum create|add-literal · data generalize
         data exchange-item create|add-element · data assign
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
| Functional chains | `capcli chain list / show` | Ordered steps with the exchange items on each chain link, entry and exit functions, pre/postcondition text, and integrity issues |
| Diagrams | `capcli diagrams list / render` | Lists diagrams (name, type, target) and renders one to SVG, as saved in the `.aird` |
| Capabilities and missions | `capcli capability list / show`, `capcli mission show` | Involved components, actors or entities, functions and chains; realizations up and down; include, extend and generalize relations in both directions; exploiting missions (SA); scenarios; pre/postcondition text; and an `issues` list |
| Interfaces | `capcli interface list L`, `capcli interface show`, and `show` | Exchange items carried, components and ports that provide or require it, allocations, and an `issues` list (no items; required but not provided) |
| Physical architecture | `capcli pa list nodes/behaviors/links/paths`, `capcli pa show`, and `show` | Node and behaviour components with where they are deployed; physical ports and their links; links with their end nodes and allocated component exchanges; paths as ordered hops; the categories of each link; an `issues` list (exchanges allocated to a link or path whose components aren't deployed at its ends, undeployed behaviour components, unconnected ports) |
| Modes and states | `capcli mode list L`, `capcli mode show`, and `show` | State machines with their owner; the tree of regions, states/modes and pseudo-states; transitions with triggers, effects, guard and trigger description; entry/exit/do activities; what is available in each state; the states a state realizes; an `issues` list |
| Data model | `capcli data types --layer L`, `capcli data show`, and `show` | Types usable from a layer (its own and those of the layers above, including SA's predefined types); classes and unions with typed properties and multiplicities; super- and sub-classes; collections with item type and multiplicity; enumeration literals; exchange items with their elements and the exchanges/ports that carry them; where each type is used; an `issues` list |
| Progress status | `capcli status values / list`, and `show` | The project's status values, elements grouped by status, and each element's status in `show` |
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
| Components (entities in OA), actors | ✅ | ✅ | ✅ | `--actor`. `--nature node\|behavior` in PA. The Part that Capella needs is created automatically. Arcadia rules are enforced: no components inside the SA System (it's a black box; decompose in LA), actors only in the Structure package (`<layer>:structure`), and in LA/PA no second top-level component next to the logical/physical system (it would break capellambse; `check` reports existing ones). `is_actor` can't be toggled with `set` |
| Functional exchanges | ✅ | ✅ | ✅ | Ports are created automatically. In OA, activities are connected directly |
| Component exchanges (communication means in OA) | ✅ | ✅ | ✅ | `--kind flow\|delegation\|assembly`. Component ports are created automatically |
| Function → component allocation | ✅ | ✅ | ✅ | `allocate`, `unallocate`, `allocate --move` |
| Functional exchange → component exchange allocation | ✅ | | ✅ | `allocate`, `unallocate` |
| Realization (function, component, chain, capability, state, mode or transition → the layer above) | ✅ | | ✅ | `realize`, `unrealize`. Both ends of the link are written, as Capella does |
| Functional chains / operational processes | ✅ | ✅ | ✅ | `chain create / add / remove`, with `--path` |
| Exchange items on a chain link | ✅ | | ✅ | `chain items <chain> <exchange> <item>... [--remove]`. A warning tells you when the exchange itself doesn't carry the item |
| Pre- and postconditions of capabilities and chains | ✅ | ✅ | ✅ | `capability condition` / `chain condition` with `--pre`, `--post`, `--clear-pre`, `--clear-post`. Plain text, written as a Constraint like Capella does |
| Capabilities (OperationalCapability, Capability, CapabilityRealization) | ✅ | ✅ | ✅ | `capability create` picks the metaclass for the layer. Name and description with `set` |
| Involvement in a capability: components, actors, entities, functions, chains | ✅ | | ✅ | `capability involve` / `uninvolve`. `chain involve` still works too |
| Capability include / extend / generalize | ✅ | | ✅ | `capability include`, `extend` or `generalize`, each with `--remove` |
| Missions (SA): exploited capabilities, involved actors | ✅ | | ✅ | `mission create`, `mission exploit`, `mission involve` (`--remove` to undo) |
| Text and simple attributes of any element | | ✅ | | `set`: name, description, summary, review, sid, booleans, numbers, and enumerations such as function `kind`, PA `nature` or exchange item `type`. A wrong enumeration value is rejected with the list of allowed values |
| Interfaces and the exchange items they carry | ✅ | ✅ | ✅ | `interface create`, `interface items`. An interface is visible from its own layer and the layers below |
| Interface provided / required by a component or component port; interface allocation | ✅ | | ✅ | `interface provide`, `interface require`, `interface allocate` (each with `--remove`) |
| Packages (function, component, capability, data, interface) | ✅ | ✅ | ✅ | `package create --parent …`: the kind follows from the parent. Shortcuts `<layer>:functions`, `:structure`, `:capabilities`, `:data`, `:interfaces`. Packages are deleted like any element |
| Order of an element among its siblings | | ✅ | | `reorder <element> --before/--after <sibling>`, or `--first` / `--last`: the order Capella's project explorer shows |
| Moving an element to another parent | | ✅ | | `move <element> <new-parent>`: functions, components, actors, packages, capabilities, data elements, chains. Same layer only; Arcadia rules apply; the component's Part moves with it, and exchanges/physical links are re-homed |
| Repairing structure violations | | ✅ | | `repair structure` (preview with `--dry-run`): actors inside components go to the Structure package. SA sub-systems are reported with options, because the fix is a modelling decision |
| Physical ports and links (PA) | ✅ | ✅ | ✅ | `pa port`, `pa link` (ports created automatically). Only between node components |
| Deployment of behaviour (or node) components on nodes | ✅ | | ✅ | `pa deploy` / `pa deploy --remove`. A node is never deployed on a behaviour component |
| Physical link categories | ✅ | ✅ | ✅ | `pa category --parent …`, `pa categorize <category> <link>... [--remove]`. Deleting a link detaches it from its categories. No Capella-written example was available: check the first one in Capella |
| Physical paths | ✅ | | ✅ | `pa path --link … --link …` from consecutive links. Deleting a link with `--cascade` also deletes the paths through it |
| Component exchange → physical link/path; component port → physical port | ✅ | | ✅ | `allocate` / `unallocate` |
| State machines, states, modes and pseudo-states (initial, final, terminate, choice, fork, join, shallow/deep history, entry/exit point), sub-states | ✅ | ✅ | ✅ | `mode machine create`, `mode add`. A state machine never mixes modes and states (sub-regions included), as Arcadia recommends, and each region holds one initial state at most. `mode show` reports machines that already mix them |
| Transitions (triggers, effects, guard, trigger description) | ✅ | | ✅ | `mode transition`. Deleting a state deletes its transitions (with `--cascade`); deleting an effect function or trigger only detaches it |
| Functions, chains and capabilities available in a state or mode | ✅ | | ✅ | `mode available <state> <element>... [--remove]` |
| Entry, exit and do activities of a state or mode | ✅ | | ✅ | `mode activity <state> --entry/--exit/--do <function> [--remove]`. Functions of the same layer. Deleting the function only detaches it |
| Classes, unions and properties (type, multiplicity, association/aggregation/composition) | ✅ | ✅ | ✅ | `data class create`, `data union`, `data property add`. A property's type must come from the same layer or a layer above. Plain attributes have no aggregation kind (`--kind unset`, the default), as in Capella; union members are written as `UnionProperty` |
| Basic types (boolean, integer, float, string, physical quantity) | ✅ | ✅ | ✅ | `data type --kind …`. Booleans get their True/False literals, as in Capella. Units of physical quantities aren't set |
| Collections | ✅ | ✅ | ✅ | `data collection --type <item type> [--min] [--max]` (default `0..*`) |
| Generalization (super-class) of classes, unions, enumerations, collections | ✅ | | ✅ | `data generalize <element> <super> [--remove]`. Same kind, visible layer, no cycles |
| Enumerations and literals | ✅ | ✅ | ✅ | `data enum create --literal …`, `data enum add-literal` |
| Exchange items and their elements | ✅ | ✅ | ✅ | `data exchange-item create --mechanism …`, `add-element`. Deleting an item detaches it from its carriers |
| Exchange items carried by functional exchanges, function ports, component exchanges | ✅ | | ✅ | `data assign <item> <carrier>... [--remove]` |
| Progress status (DRAFT, TO_BE_REVIEWED, …) of any element | | ✅ | ✅ | `status set VALUE <uuid>...` or `set <uuid> status=VALUE`. Only the project's ProgressStatus values are accepted. `NOT_SET` clears |
| Existing requirement text | | ✅ | | `set <req> text="<p>…</p>"` |
| Existing property values (PVMT) | | ✅ | | `set <property value> value=…` |
| Any element inside a layer | | | ✅ | `delete` refuses while the element is still referenced (a component's own Parts go with it). `--cascade` also removes the exchanges, allocations, realizations, involvements and ports that depend on it, but only *links* whose end is deleted: an element that merely points to it (a capability's precondition, a transition's guard, a port's interface) is kept and the reference cleared. Ports that still provide interfaces or carry items are kept. The result warns about functional chains that lost a part (`affected_chains`) and texts that still link to a deleted element (`text_links`). A layer's root packages, function and component are never deleted |
| Several changes at once | ✅ | ✅ | ✅ | `batch`: all or nothing, and later steps can refer to elements created earlier (`"as"` / `"$name"`; `$$` for a literal `$name`) |

### Not possible today

These elements can all be **read** with `search`, `show` and `show --attr`.
Changing them needs Capella, or a reviewed capellambse script.

| Area | What is missing |
|---|---|
| Structure | Moving states, ports, exchanges or elements across layers. Automatic fixing of SA sub-systems |
| Scenarios | Scenarios, instance roles, sequence messages, fragments (see Future perspectives) |
| Modes and states | Moving a state to another region |
| Data model | Units and value ranges of physical quantities, default/min/max values of properties, data values in general |
| Ports | Creating component or function ports on their own: they come into being with an exchange (physical ports: `pa port`) |
| Physical architecture | Physical path realizations (they belong to EPBS, which capcli doesn't cover), moving a component to another node part |
| Functional chains | Control nodes and sequence links of complex chains (AND/OR/ITERATE). They are kept but not editable, and `chain show` leaves them out. Exchange contexts (see Future perspectives) |
| Requirements | Creating requirements, or linking them to model elements (see Future perspectives). Existing text and attributes can be changed with `set` |
| Property values (PVMT) | Creating property values or groups, or applying them to elements. Existing values can be changed with `set` |
| Constraints | Free constraints on other elements (only guards and capability/chain pre/postconditions are written) |
| Diagrams | Creating diagrams, or adding new elements to them: new elements exist in the model but aren't drawn. Deleted elements stay on diagrams until you clean them up in Capella (`delete` warns you). `diagrams render` shows the layout as saved |

### Future perspectives

These were left out on purpose for now. They are the next candidates:

- **Requirements**: create requirements in a requirement module and link or
  unlink them to model elements. capellambse can write them, but the relation
  direction it writes (`CapellaIncomingRelation`) must first be checked
  against relations written by Capella.
- **Scenarios**: exchange, functional and interface scenarios, with instance
  roles, sequence messages, executions and fragments. Capella has strict
  ordering rules for messages and their start/end events, so this needs
  careful XML work.
- **Complex functional chains**: control nodes (AND/OR/ITERATE), sequence
  links and exchange contexts.
- **EPBS** and physical path realizations.
- **Automatic fix of SA sub-systems**: today `repair structure` only reports
  them, because turning one into an actor or an LA component is a modelling
  decision.
- **Physical quantity units** and data values (default/min/max values).
- **Diagrams**: creating diagrams or drawing new elements is out of reach
  with capellambse.

Two features follow capellambse's metamodel because the test model has no
Capella-written example: **interface allocations** and **physical link
categories**. Open the first one you create in Capella to confirm it.

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

See [`CLAUDE.md`](CLAUDE.md) for the architecture, the capellambse traps
capcli works around, the rules to keep, and a checklist for adding features.

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Tests run against capellambse's Capella 7.0 test model, vendored in
`tests/data/model` (© DB InfraGO AG, Apache-2.0, see the `.license` files).
