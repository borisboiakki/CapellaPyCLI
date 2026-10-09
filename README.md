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
| capellambse can't edit chains through `involved_*`, and `capability.involved_chains.append()` rejects chains | `chain` commands create the involvements directly and keep links and functions consistent |
| Partial writes after an error | `batch` applies a list of steps all-or-nothing |
| Hard to tell if the model is still sound | `check` (reference integrity) and `validate` (capellambse rules) |

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
verify:  check · validate
global:  --model PATH (or $CAPELLA_MODEL) · --dry-run
```

See `templates/AGENTS.md` for a full cheat sheet, or `capcli <command> --help`.

## Limits

- Diagram layout isn't generated: new elements exist in the model but aren't
  drawn on existing diagrams. `diagrams render` shows diagrams as they were saved.
- `delete` can't fix diagrams that show deleted elements. It warns you, so you
  can clean them up in Capella.
- Functional chains: simple chains (functions + exchanges) are fully supported.
  Control nodes and sequence links of complex chains (AND/OR/ITERATE) are kept
  but can't be edited, and `chain show` ignores them. Exchange contexts and
  exchanged items on chain links aren't editable either.
- Deeper changes (scenarios, data models, PVMT) need a
  capellambse script for now. They are good candidates for new `ops`.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Tests run against capellambse's Capella 7.0 test model, vendored in
`tests/data/model` (© DB InfraGO AG, Apache-2.0, see the `.license` files).
