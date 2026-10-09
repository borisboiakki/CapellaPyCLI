---
name: capella-model
description: Read, query and edit the Capella MBSE model (.aird/.capella files) through the capcli command, covering functions, components, exchanges, allocations, realizations, functional chains, capabilities, missions, progress status and the data model (classes, enumerations, exchange items) in the OA/SA/LA/PA layers. Use for any question about the system model or any change to it, and never edit the model files directly.
compatibility: Needs the capcli command on PATH (pip install from the CapellaPyCLI repo, Python 3.10+). Works with OpenCode and Claude Code.
metadata:
  tool: capcli
  layers: oa sa la pa
---

# Capella model via capcli

<!-- Copy this folder to `.opencode/skills/capella-model/` in the repository
     that holds the model (OpenCode also finds it under `.claude/skills/` and
     `.agents/skills/`). Adjust the values in "Project settings". -->

The system model is a Capella project. **Read and change it only through the
`capcli` command.** Never edit `.capella`, `.aird` or `.afm` files by hand, or
with sed or search-and-replace: elements are linked by UUID cross-references,
and a text edit silently breaks them.

## Project settings (ADJUST)

| Setting | Value |
|---|---|
| Model | `model/MyModel.aird` (`export CAPELLA_MODEL=model/MyModel.aird`) |
| Layers you may modify | `sa`, `la` (only read `oa` and `pa` unless asked) |
| Functions | verb + object, lower case: "compute route" |
| Components | noun phrase, Title Case: "Navigation Unit" |
| Exchanges | the data or item conveyed: "position", "route request" |

If `CAPELLA_MODEL` isn't set, capcli uses the only `.aird` under the current directory.

## Arcadia structure rules

capcli enforces these, and `capcli check` reports any violations already in
the model:

- **System Analysis is a black box.** The System is the only SA component.
  Never create sub-systems in SA: decompose the system in the Logical
  Architecture (`la:root-component`).
- **Actors are outside the system.** In SA, LA and PA, create them in the
  Structure package (`--parent <layer>:structure`), never inside a component.
  An external system, such as a ground station, is an actor.

## Workflow

1. **Before writing:**
   - Make sure Capella is closed. If you are unsure, ask the user, because an
     open Capella overwrites your changes.
   - Check that you're on a git branch and the model is committed (`git status`).
2. **Look up before you write.** Get UUIDs from `capcli search`, `list`, `tree`
   or `show`. Never invent UUIDs or reuse them from memory or another model.
3. **Plan the change as one `capcli batch`** when it has more than one step.
   A batch is all-or-nothing.
4. **Preview** with `capcli --dry-run …`, which you must do before any
   `delete --cascade`. Show the user what would be removed.
5. **Apply**, then **verify**:
   - `capcli check` must report `"ok": true`;
   - run `capcli validate --layer <layer>`;
   - for every chain you touched, `capcli chain show <chain>` must have an empty
     `issues` list, unless the user asked for a partial chain;
   - for every capability you touched, check `capcli capability show <cap>`.
     Every SA/LA/PA capability should realize one in the layer above, and
     involve the components that its functions are allocated to.
6. **Report:**
   - summarize what changed, with names and UUIDs, and show `git diff --stat`;
   - list the diagrams the user must update in Capella, because new elements
     are not drawn automatically.
7. If capcli can't do something, say so and propose a reviewed Python script
   using `capellambse`. Never fall back to raw XML edits.

## Reading the output

- Every command prints one JSON document.
- Errors print `{"error": "..."}` and exit with code 1. `check` exits with code 2
  when it finds broken references.
- Wherever a command takes an element, you can give a UUID or a shortcut:
  `la:root-function`, `la:root-component`, `la:structure` (the package for
  actors), `oa:root-activity`,
  `oa:root-entity` (likewise for `sa` and `pa`).

## Commands

```bash
# Read
capcli info                                   # layers + element counts
capcli list la                                # kinds available in a layer
capcli list la functions --name route         # filter by name substring
capcli search "Navigation" --layer la         # by name (Parts/ports hidden)
capcli search --type LogicalComponent --layer la
capcli show <uuid>                            # element + relations, chains, diagrams
capcli show <uuid> --attr realized_functions  # any attribute
capcli tree la:root-component --depth 2       # breakdown with allocations
capcli diagrams list --type LAB
capcli diagrams render <diagram-uuid> -o /tmp/lab.svg

# Write (OA uses activities, entities and communication means automatically)
capcli create function  --parent <fn|la:root-function> --name "compute route"
capcli create component --parent <comp|la:root-component> --name "Navigator"  # LA/PA only
capcli create component --parent sa:structure --name "Operator" --actor   # actors: Structure pkg
capcli create component --parent oa:root-entity --name "Crew"             # OA entity
capcli create component --parent pa:root-component --name ECU --nature node
capcli create function-exchange  --source <fn> --target <fn> --name "position"
capcli create component-exchange --source <comp> --target <comp> --name "CAN" [--kind flow]
capcli allocate <function> <component> [--move]
capcli allocate <functional-exchange> <component-exchange>
capcli unallocate <function> <component>
capcli realize <lower-element> <upper-element>  # function, component, chain or capability
capcli set <uuid> name="new name" description="<p>html</p>"
capcli delete <uuid>                          # refuses while referenced
capcli --dry-run delete <uuid> --cascade      # preview what cascade removes

# Functional chains (operational processes in OA)
capcli chain list la [--involving <fn-or-exchange>]
capcli chain show <chain>                     # ordered steps, entry/exit, issues
capcli chain create --layer la --name "Navigate" \
    --path <fn1> --path <fn2> --path <fn3>    # consecutive fns joined by their exchange
capcli chain add <chain> <exchange|function>...     # an exchange brings both functions
capcli chain remove <chain> <exchange|function>...  # out of the chain only
capcli chain involve <chain> <capability>

# Capabilities (OperationalCapability in OA, Capability in SA,
# CapabilityRealization in LA/PA)
capcli capability list sa [--involving <element>]
capcli capability show <cap>                  # involvements, realizations, relations,
                                              # missions, scenarios, issues
capcli capability create --layer sa --name "Navigate to destination"
capcli capability involve <cap> <component|actor|entity|function|chain>...
capcli capability uninvolve <cap> <element>...    # removes the link only
capcli capability include <cap> <other> [--remove]
capcli capability extend <cap> <other> [--remove]
capcli capability generalize <cap> <more-general-cap> [--remove]
capcli realize <la-capability> <sa-capability>    # sa->oa, la->sa, pa->la
capcli unrealize <element> <realized>         # works for every kind of realization

# Data model (classes, enumerations, exchange items)
capcli data types --layer la [--name int]     # types usable from LA (own + layers above)
capcli data class create --layer la --name "Position"
capcli data property add <class> --name lat --type <type> [--min 0] [--max '*'] [--kind composition]
capcli data enum create --layer la --name NavMode --literal AUTO --literal MANUAL
capcli data enum add-literal <enum> SAFE
capcli data exchange-item create --layer la --name PositionMsg --mechanism flow
capcli data exchange-item add-element <exchange-item> --name pos --type <type> [--min/--max]
capcli data assign <exchange-item> <functional-exchange|function-port|component-exchange>... [--remove]
capcli data show <uuid>                       # properties, literals, elements, usage, issues

# Progress status (the project's ProgressStatus values only)
capcli status values                          # DRAFT, TO_BE_REVIEWED, … REVIEWED_OK
capcli status set TO_BE_REVIEWED <uuid>...    # NOT_SET clears; also: set <uuid> status=DRAFT
capcli status list [VALUE] [--layer la]       # elements grouped by status

# Missions (SA)
capcli mission create --name "Get there"
capcli mission show <mission>
capcli mission exploit <mission> <sa-capability> [--remove]
capcli mission involve <mission> <actor>... [--remove]

# Verify
capcli check                                  # exit 2 if references are broken
capcli check --fix                            # fill in missing realization sources
capcli validate --layer la [--all]
```

A chain `--path` needs exactly one exchange between each pair of consecutive
functions. Create any missing ones first, and `chain add` the right exchange
when there are several.

## Batch

```bash
capcli batch <<'EOF'
[
  {"op": "create-function", "as": "acq", "parent": "la:root-function", "name": "acquire position"},
  {"op": "create-function", "as": "rte", "parent": "la:root-function", "name": "compute route"},
  {"op": "create-function-exchange", "as": "pos", "source": "$acq", "target": "$rte", "name": "position"},
  {"op": "create-component", "as": "nav", "parent": "la:root-component", "name": "Navigator"},
  {"op": "allocate", "element": "$acq", "to": "$nav"},
  {"op": "allocate", "element": "$rte", "to": "$nav"},
  {"op": "create-chain", "layer": "la", "name": "Navigate", "path": ["$acq", "$rte"]}
]
EOF
```

`"as"` names the element a create step makes, and `"$name"` refers to it in
later steps. The ops and their arguments:

| op | arguments |
|---|---|
| `create-function` | `parent`, `name`, `description` |
| `create-component` | `parent`, `name`, `actor`, `nature`, `description` |
| `create-function-exchange` | `source`, `target`, `name` |
| `create-component-exchange` | `source`, `target`, `name`, `kind` |
| `allocate` | `element`, `to`, `move` |
| `unallocate` | `element`, `from` |
| `realize` | `element`, `realized` |
| `set` | `element`, `values` (object) |
| `delete` | `element`, `cascade` |
| `create-chain` | `name`, `layer` or `parent`, `path`, `elements`, `kind`, `description` |
| `chain-add` / `chain-remove` | `chain`, `elements` |
| `involve-chain` | `chain`, `capability` |
| `unrealize` | `element`, `realized` |
| `create-capability` | `name`, `layer` or `parent`, `description` |
| `capability-involve` / `capability-uninvolve` | `capability`, `elements` |
| `capability-include` / `capability-extend` / `capability-generalize` | `capability`, `other`, `remove` |
| `create-mission` | `name`, `description` |
| `mission-exploit` | `mission`, `capability`, `remove` |
| `mission-involve` | `mission`, `elements`, `remove` |
| `set-status` | `value`, `elements` |
| `create-class` | `name`, `layer` or `parent`, `description` |
| `add-property` | `class`, `name`, `type`, `min`, `max`, `kind`, `description` |
| `create-enumeration` | `name`, `layer` or `parent`, `literals`, `description` |
| `add-literals` | `enumeration`, `literals` |
| `create-exchange-item` | `name`, `layer` or `parent`, `mechanism`, `description` |
| `add-exchange-item-element` | `exchange_item`, `name`, `type`, `min`, `max` |
| `assign-exchange-item` | `exchange_item`, `elements`, `remove` |
