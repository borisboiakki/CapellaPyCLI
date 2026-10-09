# Working on the Capella model

<!-- Copy this file into the root of the repository that holds your Capella
     model (Claude Code also reads it if CLAUDE.md contains `@AGENTS.md`).
     Adjust the sections marked ADJUST. -->

The system model is a Capella project. **Read and change it only through the
`capcli` command.** Never edit `.capella`, `.aird` or `.afm` files by hand or
with sed/grep-and-replace: elements are linked by UUID cross-references, and a
text edit silently breaks them.

## Setup

- Model: `model/MyModel.aird` (ADJUST). It is set by `export CAPELLA_MODEL=model/MyModel.aird`,
  or capcli picks the only `.aird` under the current directory.
- Capella must be **closed** while you write, or it will overwrite your changes.
  If you are unsure, ask the user before writing.
- Work on a git branch. Commit the model before a series of edits so it can be reverted.

## Ground rules

1. **Look before you write.** Get UUIDs from `capcli search` / `capcli list` /
   `capcli tree`. Never invent UUIDs, and never reuse UUIDs from memory or from
   another model.
2. **Respect the Arcadia structure.** capcli enforces this, but plan for it:
   - **System Analysis is a black box.** The System is the only SA component.
     Never create sub-systems in SA: decompose the system in the Logical
     Architecture (`la:root-component`).
   - **Actors are outside the system.** In SA, LA and PA, create them in the
     Structure package (`--parent <layer>:structure`), never inside a component.
     An external system (e.g. a ground station) is an actor.
3. **Allowed layers** (ADJUST): `sa`, `la`. Do not modify `oa` or `pa` unless asked.
4. **Preview risky changes** with `capcli --dry-run ...` first, especially `delete --cascade`.
5. **Group related changes** into one `capcli batch`: it is all-or-nothing.
6. **After every write session**, run `capcli check` (it must report `"ok": true`)
   and `capcli validate --layer <layer>`, then show the user `git diff --stat`.
7. After changing a functional chain, run `capcli chain show <chain>`. Its
   `issues` list must be empty, unless the user asked for a partial chain.
   A chain path only works if the exchanges between its functions exist;
   create any that are missing first.
8. After changing a capability, run `capcli capability show <cap>` and look
   at its `issues`. Every SA/LA/PA capability should realize one in the layer
   above, and involve the components that its functions are allocated to.
9. A region holds either **modes** or **states**, never both, and each
   sub-region gets at most one initial state. capcli enforces this. Check
   `capcli mode show <machine>` for `issues` after editing a state machine.
10. In PA, physical ports and links connect **node** components; behaviour
    components are **deployed** on nodes (`capcli pa deploy`). A component
    exchange allocated to a link or path must run between components deployed
    on the nodes at its ends: check `capcli pa show <link|path>` for `issues`.
11. To reorganise the model, use `capcli move` (it keeps Parts and exchanges
    consistent), never delete-and-recreate, which loses UUIDs and links. If
    `capcli check` reports `structure` problems, run `capcli --dry-run repair
    structure`, show the user the result, and apply it if they agree.
12. New elements do not appear on existing diagrams. Tell the user which
   diagrams need updating in Capella, because capcli can't lay them out.
13. If capcli cannot do something, say so and propose a reviewed Python script
    using `capellambse`. Don't work around it with raw XML edits.

## Naming conventions (ADJUST)

- Functions: verb + object, lower case ("compute route", "acquire position").
- Components: noun phrase, Title Case ("Navigation Unit").
- Exchanges: the data/item conveyed ("position", "route request").

## capcli cheat sheet

Every command prints JSON. Errors are `{"error": "..."}` with exit code 1.
Element arguments take a UUID or a shortcut: `la:root-function`,
`la:root-component`, `la:structure` (the package for actors), `la:functions`,
`la:capabilities`, `la:data`, `la:interfaces` (root packages), `oa:root-activity`,
`oa:root-entity` (same for `sa`, `pa`).

```bash
# Read
capcli info                                   # layers + element counts
capcli list la                                # kinds available in a layer
capcli list la functions --name route         # filter by name substring
capcli search "Navigation" --layer la         # by name, whole model or one layer
capcli search --type LogicalComponent --layer la
capcli show <uuid>                            # element + parent, children, ports,
                                              # exchanges, allocation, diagrams
capcli show <uuid> --attr realized_functions  # any attribute
capcli tree la:root-component --depth 2       # breakdown with allocations
capcli diagrams list --type LAB
capcli diagrams render <diagram-uuid> -o /tmp/lab.svg

# Write  (layers: oa | sa | la | pa; OA uses activities/entities automatically)
capcli create function  --parent <fn-uuid|la:root-function> --name "compute route"
capcli create component --parent <comp-uuid|la:root-component> --name "Navigator"  # LA/PA only
capcli create component --parent sa:structure --name "Operator" --actor   # actors: Structure pkg
capcli create component --parent oa:root-entity --name "Crew"             # OA entity
capcli create component --parent pa:root-component --name ECU --nature node
capcli create function-exchange  --source <fn> --target <fn> --name "position"
capcli create component-exchange --source <comp> --target <comp> --name "CAN" [--kind flow]
capcli allocate <function> <component> [--move]
capcli allocate <functional-exchange> <component-exchange>
capcli unallocate <function> <component>
capcli realize <la-function> <sa-function>    # traceability to the layer above
capcli set <uuid> name="new name" description="<p>html</p>"
capcli delete <uuid>                          # refuses while referenced
capcli --dry-run delete <uuid> --cascade      # preview what cascade removes

# Functional chains (operational processes in OA)
capcli chain list la [--involving <fn-or-exchange>]
capcli chain show <chain>                     # ordered steps, entry/exit, issues
capcli chain create --layer la --name "Navigate" \
    --path <fn1> --path <fn2> --path <fn3>    # consecutive fns joined by their exchange
capcli chain add <chain> <exchange|function>...   # an exchange brings both its functions
capcli chain remove <chain> <exchange|function>...  # takes them out of the chain only
capcli chain involve <chain> <capability>
capcli realize <la-chain> <sa-chain>

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

# Modes and states
capcli mode list sa                           # state machines of a layer, with owner
capcli mode machine create sa:root-component  # "<owner> State Machine" + Default Region
capcli mode add <machine|region|state> --name Off [--kind state|mode|initial|final|choice|…]
capcli mode transition <source> <target> [--trigger <exchange|item>] [--effect <function>] \
    [--guard "power > 10"] [--trigger-description "power on"]
capcli mode available <state> <function|chain|capability>... [--remove]
capcli mode show <machine|state>              # tree, transitions, availability, issues

# Physical architecture (PA)
capcli pa list nodes|behaviors|links|paths    # behaviors include the node they run on
capcli pa port <node> --name eth0             # physical ports only on node components
capcli pa link --source <node|port> --target <node|port> --name CAN-A   # ports auto-created
capcli pa deploy <behaviour-or-node> <host> [--remove]   # never a node on a behaviour component
capcli pa path --name cam-to-ecu --link <link1> --link <link2>   # consecutive links
capcli allocate <component-exchange> <physical-link|physical-path>
capcli allocate <component-port> <physical-port>
capcli pa show <component|port|link|path>     # deployment, hops, allocations, issues

# Structure: packages, moving, repair
capcli package create --parent <la:functions|la:structure|la:capabilities|la:data|la:interfaces|pkg|function|component> --name Avionics
capcli move <element> <new-parent>            # same layer; Part and exchanges follow
capcli --dry-run repair structure             # preview, then without --dry-run to apply

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
capcli check                                  # dangling/empty/incomplete references (exit 2 if bad)
capcli check --fix                            # fill in missing realization sources
capcli validate --layer la [--all]
```

### Batch (atomic, with aliases)

```bash
capcli batch <<'EOF'
[
  {"op": "create-function", "as": "acq", "parent": "la:root-function", "name": "acquire position"},
  {"op": "create-function", "as": "rte", "parent": "la:root-function", "name": "compute route"},
  {"op": "create-function-exchange", "source": "$acq", "target": "$rte", "name": "position"},
  {"op": "create-component", "as": "nav", "parent": "la:root-component", "name": "Navigator"},
  {"op": "allocate", "element": "$acq", "to": "$nav"},
  {"op": "allocate", "element": "$rte", "to": "$nav"},
  {"op": "set", "element": "$rte", "values": {"description": "<p>Computes the route.</p>"}}
]
EOF
```

Ops: `create-function`, `create-component`, `create-function-exchange`,
`create-component-exchange`, `allocate`, `unallocate` (`"from"`), `realize`
(`"element"`, `"realized"`), `set` (`"values"`), `delete` (`"cascade"`),
`create-chain` (`"name"`, `"layer"` or `"parent"`, `"path"`, `"elements"`, `"kind"`),
`chain-add` / `chain-remove` (`"chain"`, `"elements"`), `involve-chain`
(`"chain"`, `"capability"`), `unrealize` (`"element"`, `"realized"`),
`create-capability` (`"name"`, `"layer"` or `"parent"`, `"description"`),
`capability-involve` / `capability-uninvolve` (`"capability"`, `"elements"`),
`capability-include` / `capability-extend` / `capability-generalize`
(`"capability"`, `"other"`, `"remove"`), `create-mission` (`"name"`),
`mission-exploit` (`"mission"`, `"capability"`, `"remove"`), `mission-involve`
(`"mission"`, `"elements"`, `"remove"`), `set-status` (`"value"`, `"elements"`),
`create-class` / `create-enumeration` / `create-exchange-item` (`"name"`, `"layer"`
or `"parent"`, plus `"literals"` / `"mechanism"`), `add-property` (`"class"`,
`"name"`, `"type"`, `"min"`, `"max"`, `"kind"`), `add-literals` (`"enumeration"`,
`"literals"`), `add-exchange-item-element` (`"exchange_item"`, `"name"`, `"type"`,
`"min"`, `"max"`), `assign-exchange-item` (`"exchange_item"`, `"elements"`, `"remove"`),
`create-state-machine` (`"owner"`, `"name"`), `add-state` (`"parent"`, `"name"`,
`"kind"`), `add-transition` (`"source"`, `"target"`, `"triggers"`, `"effects"`,
`"guard"`, `"trigger_description"`), `set-available` (`"state"`, `"elements"`, `"remove"`),
`create-physical-port` (`"component"`, `"name"`), `create-physical-link` (`"source"`,
`"target"`, `"name"`), `create-physical-path` (`"name"`, `"links"`, `"parent"`), `deploy`
(`"element"`, `"host"`, `"remove"`), `create-package` (`"parent"`, `"name"`), `move`
(`"element"`, `"to"`).
`"as"` names the element created by a step, and `"$name"` refers to it in a later step.
