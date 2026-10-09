"""`capcli` command line entry point.

Every command prints a single JSON document on stdout. Errors are printed
as ``{"error": "..."}`` with exit status 1, so agents can parse both paths.
"""

from __future__ import annotations

import functools
import json
import pathlib
import sys
import warnings
from typing import Any

import click

from . import __version__, chains, ops
from .model import (
    LAYERS,
    CapError,
    brief,
    detail,
    find_model,
    is_element,
    layer,
    layer_key,
    load,
    resolve,
    type_name,
)


def emit(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


class Ctx:
    def __init__(self, model_path: str | None, dry_run: bool):
        self._path = model_path
        self.dry_run = dry_run
        self._model = None

    @property
    def path(self) -> pathlib.Path:
        return find_model(self._path)

    @property
    def model(self):
        if self._model is None:
            self._model = load(self.path)
        return self._model

    def save(self) -> bool:
        if self.dry_run:
            return False
        self.model.save()
        return True


def handled(fn):
    """Turn CapError (and unexpected errors) into JSON on stdout + exit 1."""

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except CapError as e:
            emit({"error": str(e)})
            sys.exit(1)
        except click.exceptions.Exit:
            raise
        except Exception as e:  # noqa: BLE001
            emit({"error": f"{type(e).__name__}: {e}"})
            sys.exit(1)

    return wrapper


def write_command(fn):
    """Decorator for commands that mutate and save the model."""

    @functools.wraps(fn)
    @click.pass_obj
    @handled
    def wrapper(ctx: Ctx, *a, **kw):
        result = fn(ctx.model, *a, **kw)
        saved = ctx.save()
        emit({**result, "saved": saved, **({"dry_run": True} if ctx.dry_run else {})})

    return wrapper


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--model",
    "-m",
    envvar="CAPELLA_MODEL",
    help="Path to the .aird file (or its folder). Defaults to $CAPELLA_MODEL, "
    "then the only .aird below the current directory.",
)
@click.option("--dry-run", is_flag=True, help="Apply changes in memory only; do not save.")
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context, model: str | None, dry_run: bool) -> None:
    """Read and edit Capella models from the command line (JSON output)."""
    ctx.obj = Ctx(model, dry_run)


# ---------------------------------------------------------------------- read


@cli.command()
@click.pass_obj
@handled
def info(ctx: Ctx) -> None:
    """Model metadata and element counts per layer."""
    m = ctx.model
    layers = {}
    for key in LAYERS:
        lay = layer(m, key)
        counts = {}
        for attr in dir(type(lay)):
            if attr.startswith("all_"):
                try:
                    counts[attr[4:]] = len(getattr(lay, attr))
                except Exception:  # noqa: BLE001
                    pass
        layers[key] = {"name": lay.name, "uuid": lay.uuid, "counts": counts}
    emit(
        {
            "path": str(ctx.path),
            "name": m.name,
            "capella_version": m.info.capella_version,
            "diagrams": len(m.diagrams),
            "layers": layers,
        }
    )


def _kinds(lay) -> list[str]:
    return sorted(a[4:].replace("_", "-") for a in dir(type(lay)) if a.startswith("all_"))


@cli.command("list")
@click.argument("layer_name", metavar="LAYER", type=click.Choice(LAYERS))
@click.argument("kind", required=False)
@click.option("--name", "name_filter", help="Case-insensitive substring filter on name.")
@click.option("--limit", type=int, default=500, show_default=True)
@click.pass_obj
@handled
def list_(ctx: Ctx, layer_name: str, kind: str | None, name_filter: str | None, limit: int) -> None:
    """List elements of KIND in LAYER (omit KIND to see available kinds).

    Example: capcli list la functions --name route
    """
    lay = layer(ctx.model, layer_name)
    if not kind:
        emit({"layer": layer_name, "kinds": _kinds(lay)})
        return
    attr = "all_" + kind.replace("-", "_")
    if not hasattr(type(lay), attr):
        raise CapError(f"Unknown kind {kind!r} for {layer_name}; available: {', '.join(_kinds(lay))}")
    items = list(getattr(lay, attr))
    if name_filter:
        items = [i for i in items if name_filter.lower() in (getattr(i, "name", "") or "").lower()]
    out = [brief(i) for i in items[:limit]]
    emit({"layer": layer_name, "kind": kind, "count": len(items), "items": out})


@cli.command()
@click.argument("uuid")
@click.option("--attr", "attrs", multiple=True, help="Only return these attributes (repeatable).")
@click.pass_obj
@handled
def show(ctx: Ctx, uuid: str, attrs: tuple[str, ...]) -> None:
    """Show an element with its main relations (or selected attributes)."""
    obj = resolve(ctx.model, uuid)
    if chains.is_chain(obj) and not attrs:
        emit(chains.show_chain(ctx.model, uuid))
        return
    emit(detail(obj, list(attrs) or None))


@cli.command()
@click.argument("text", required=False, default="")
@click.option("--type", "types", multiple=True, help="Metaclass name, e.g. LogicalFunction (repeatable).")
@click.option("--layer", "layer_name", type=click.Choice(LAYERS))
@click.option("--exact", is_flag=True, help="Match the name exactly instead of substring.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.pass_obj
@handled
def search(ctx: Ctx, text: str, types: tuple[str, ...], layer_name: str | None, exact: bool, limit: int) -> None:
    """Find elements by name and/or metaclass across the model.

    Parts and ports are hidden unless requested with --type.
    """
    m = ctx.model
    if not text and not types:
        raise CapError("Give a search TEXT, --type, or both")
    below = layer(m, layer_name) if layer_name else None
    try:
        pool = m.search(*types, below=below) if types else m.search(below=below)
    except (ValueError, KeyError) as e:
        raise CapError(f"Bad --type: {e}") from None
    t = text.lower()
    hits = []
    for el in pool:
        name = el._element.get("name")
        if not isinstance(name, str):
            continue
        if text and not (name == text if exact else t in name.lower()):
            continue
        if not types and type_name(el).endswith(_NOISE_TYPES):
            continue
        hits.append({**brief(el), "layer": _lk(el)})
        if len(hits) >= limit:
            break
    emit({"count": len(hits), "items": hits})


# Parts and ports usually share the name of their component/exchange; hide
# them from plain name searches unless asked for with --type.
_NOISE_TYPES = ("Part", "Port")


def _lk(el):
    try:
        return layer_key(el)
    except Exception:  # noqa: BLE001
        return None


@cli.command()
@click.argument("uuid")
@click.option("--depth", type=int, default=3, show_default=True)
@click.pass_obj
@handled
def tree(ctx: Ctx, uuid: str, depth: int) -> None:
    """Show the function/component breakdown below an element.

    UUID may be a shortcut such as la:root-function or pa:root-component.
    """

    def walk(el, d):
        node = brief(el)
        if is_element(el) and type_name(el).endswith(("Function", "Activity")):
            alloc = getattr(el, "owner", None)
            if alloc is not None and is_element(alloc):
                node["allocated_to"] = brief(alloc)
        if d >= depth:
            return node
        kids = []
        for attr in ("functions", "components", "entities"):
            kids.extend(getattr(el, attr, []) or [])
        if kids:
            node["children"] = [walk(k, d + 1) for k in kids]
        return node

    emit(walk(resolve(ctx.model, uuid), 0))


@cli.command()
@click.option("--layer", "layer_name", type=click.Choice(LAYERS))
@click.option("--all", "show_all", is_flag=True, help="Include RECOMMENDED rule failures.")
@click.option("--limit", type=int, default=200, show_default=True)
@click.pass_obj
@handled
def validate(ctx: Ctx, layer_name: str | None, show_all: bool, limit: int) -> None:
    """Run capellambse's validation rules and list failures."""
    results = ctx.model.validate()
    fails = []
    for r in results.iter_results():
        if r.passed:
            continue
        cat = str(getattr(r.rule.category, "name", r.rule.category))
        if not show_all and cat == "RECOMMENDED":
            continue
        if layer_name and _lk(r.object) != layer_name:
            continue
        fails.append({"rule": r.rule.id, "category": cat, "message": r.rule.name, "element": brief(r.object)})
    emit({"failures": len(fails), "items": fails[:limit]})


@cli.command()
@click.pass_obj
@handled
def check(ctx: Ctx) -> None:
    """Check the model for dangling or empty references (run after edits)."""
    res = ops.check(ctx.model)
    emit(res)
    if not res["ok"]:
        sys.exit(2)


@cli.group()
def diagrams() -> None:
    """List and render diagrams."""


@diagrams.command("list")
@click.option("--name", "name_filter")
@click.option("--type", "dtype", help="Diagram type, e.g. LAB, SAB, PAB, LDFB.")
@click.pass_obj
@handled
def diagrams_list(ctx: Ctx, name_filter: str | None, dtype: str | None) -> None:
    """List diagrams with their UUIDs and types."""
    out = []
    for d in ctx.model.diagrams:
        dt = str(getattr(d.type, "name", d.type))
        if name_filter and name_filter.lower() not in d.name.lower():
            continue
        if dtype and dtype.upper() != dt:
            continue
        target = getattr(d, "target", None)
        out.append({"uuid": d.uuid, "name": d.name, "type": dt, "target": brief(target) if is_element(target) else None})
    emit({"count": len(out), "items": out})


@diagrams.command("render")
@click.argument("uuid")
@click.option("--out", "-o", type=click.Path(dir_okay=False), required=True, help="Output .svg path.")
@click.pass_obj
@handled
def diagrams_render(ctx: Ctx, uuid: str, out: str) -> None:
    """Render a diagram to SVG (layout as saved in the .aird)."""
    try:
        d = ctx.model.diagrams.by_uuid(uuid)
    except KeyError:
        raise CapError(f"No diagram with UUID {uuid}") from None
    svg = d.render("svg")
    pathlib.Path(out).write_text(svg, encoding="utf-8")
    emit({"diagram": {"uuid": d.uuid, "name": d.name}, "written": out, "bytes": len(svg)})


# --------------------------------------------------------------------- write


@cli.group()
def create() -> None:
    """Create functions, components and exchanges."""


@create.command("function")
@click.option("--parent", required=True, help="Parent function UUID, or e.g. la:root-function.")
@click.option("--name", required=True)
@click.option("--description")
@write_command
def create_function(model, parent, name, description):
    """Create a function (an activity in OA) below PARENT."""
    return ops.create_function(model, parent, name, description)


@create.command("component")
@click.option("--parent", required=True, help="Parent component UUID, or e.g. la:root-component.")
@click.option("--name", required=True)
@click.option("--actor", is_flag=True, help="Create an actor instead of a component.")
@click.option("--nature", type=click.Choice(["node", "behavior"], case_sensitive=False), help="PA only.")
@click.option("--description")
@write_command
def create_component(model, parent, name, actor, nature, description):
    """Create a component (an entity in OA) below PARENT."""
    return ops.create_component(model, parent, name, actor, nature, description)


@create.command("function-exchange")
@click.option("--source", required=True, help="Source function UUID.")
@click.option("--target", required=True, help="Target function UUID.")
@click.option("--name", required=True)
@write_command
def create_function_exchange(model, source, target, name):
    """Connect two functions; ports are created automatically."""
    return ops.create_function_exchange(model, source, target, name)


@create.command("component-exchange")
@click.option("--source", required=True, help="Source component UUID.")
@click.option("--target", required=True, help="Target component UUID.")
@click.option("--name", required=True)
@click.option("--kind", type=click.Choice(["unset", "flow", "delegation", "assembly"], case_sensitive=False))
@write_command
def create_component_exchange(model, source, target, name, kind):
    """Connect two components (communication mean in OA)."""
    return ops.create_component_exchange(model, source, target, name, kind)


@cli.command()
@click.argument("element")
@click.argument("to")
@click.option("--move", is_flag=True, help="Remove the function's existing allocation first.")
@write_command
def allocate(model, element, to, move):
    """Allocate a function to a component, or a functional exchange to a component exchange."""
    return ops.allocate(model, element, to, move)


@cli.command()
@click.argument("element")
@click.argument("from_", metavar="FROM")
@write_command
def unallocate(model, element, from_):
    """Remove an allocation created with `allocate`."""
    return ops.unallocate(model, element, from_)


@cli.command()
@click.argument("element")
@click.argument("realized")
@write_command
def realize(model, element, realized):
    """Trace ELEMENT as realizing REALIZED in the layer above.

    Works for functions, components and functional chains (e.g. LA fn -> SA fn).
    """
    return ops.realize(model, element, realized)


@cli.command("set")
@click.argument("uuid")
@click.argument("assignments", nargs=-1, required=True, metavar="KEY=VALUE...")
@write_command
def set_(model, uuid, assignments):
    """Set scalar attributes, e.g. `capcli set <uuid> name="Compute route" description="..."`."""
    values = {}
    for a in assignments:
        if "=" not in a:
            raise CapError(f"Expected KEY=VALUE, got {a!r}")
        k, _, v = a.partition("=")
        values[k.strip()] = v
    return ops.set_attrs(model, uuid, values)


@cli.command()
@click.argument("uuid")
@click.option("--cascade", is_flag=True, help="Also delete exchanges/allocations/realizations that reference it.")
@write_command
def delete(model, uuid, cascade):
    """Delete an element; refuses if anything still references it."""
    return ops.delete(model, uuid, cascade)


@cli.group()
def chain() -> None:
    """Functional chains (operational processes in OA)."""


@chain.command("list")
@click.argument("layer_name", metavar="LAYER", type=click.Choice(LAYERS))
@click.option("--involving", help="Only chains involving this function/exchange UUID.")
@click.pass_obj
@handled
def chain_list(ctx: Ctx, layer_name: str, involving: str | None) -> None:
    """List the chains of a layer."""
    m = ctx.model
    items = chains.all_chains(m, layer_name)
    if involving:
        target = resolve(m, involving)
        items = [c for c in items if target in c.involved]
    emit({"count": len(items), "items": [{**brief(c), "issues": len(chains._summary(c)["issues"])} for c in items]})


@chain.command("show")
@click.argument("uuid")
@click.pass_obj
@handled
def chain_show(ctx: Ctx, uuid: str) -> None:
    """Show a chain as ordered steps, with entry/exit functions and issues."""
    emit(chains.show_chain(ctx.model, uuid))


@chain.command("create")
@click.option("--name", required=True)
@click.option("--layer", "layer_name", type=click.Choice(LAYERS), help="Own the chain by the layer's root function.")
@click.option("--parent", help="Function that owns the chain (instead of --layer).")
@click.option("--path", "path", multiple=True, help="Function UUIDs in order (repeat); consecutive ones are linked by the exchange between them.")
@click.option("--add", "elements", multiple=True, help="Extra function/exchange UUIDs to involve (repeat).")
@click.option("--kind", type=click.Choice(["simple", "composite", "fragment"], case_sensitive=False))
@click.option("--description")
@write_command
def chain_create(model, name, layer_name, parent, path, elements, kind, description):
    """Create a functional chain, e.g. along a path of functions.

    \b
    capcli chain create --layer la --name "Navigate" \\
        --path <acquire-fn> --path <compute-fn> --path <display-fn>
    """
    return chains.create_chain(model, name, parent, layer_name, list(path), list(elements), kind, description)


@chain.command("add")
@click.argument("chain_uuid", metavar="CHAIN")
@click.argument("elements", nargs=-1, required=True)
@write_command
def chain_add(model, chain_uuid, elements):
    """Involve functional exchanges (with both their functions) or functions."""
    return chains.add_to_chain(model, chain_uuid, list(elements))


@chain.command("remove")
@click.argument("chain_uuid", metavar="CHAIN")
@click.argument("elements", nargs=-1, required=True)
@write_command
def chain_remove(model, chain_uuid, elements):
    """Take functions (and their links) or exchanges out of a chain.

    The functions and exchanges themselves are not deleted.
    """
    return chains.remove_from_chain(model, chain_uuid, list(elements))


@chain.command("involve")
@click.argument("chain_uuid", metavar="CHAIN")
@click.argument("capability")
@write_command
def chain_involve(model, chain_uuid, capability):
    """Record that CAPABILITY involves the chain."""
    return chains.involve_chain(model, chain_uuid, capability)


@cli.command()
@click.argument("file", type=click.File("r"), default="-")
@click.pass_obj
@handled
def batch(ctx: Ctx, file) -> None:
    """Apply a JSON list of operations atomically (all or nothing).

    \b
    Example (stdin):
      [{"op": "create-function", "as": "f1", "parent": "la:root-function", "name": "A"},
       {"op": "create-function", "as": "f2", "parent": "la:root-function", "name": "B"},
       {"op": "create-function-exchange", "source": "$f1", "target": "$f2", "name": "data"}]
    """
    try:
        steps = json.load(file)
    except json.JSONDecodeError as e:
        raise CapError(f"Invalid JSON: {e}") from None
    if not isinstance(steps, list):
        raise CapError("Batch input must be a JSON list of steps")
    results = ops.run_batch(ctx.model, steps)
    saved = ctx.save()
    emit({"steps": results, "saved": saved, **({"dry_run": True} if ctx.dry_run else {})})


def main() -> None:
    # capellambse emits deprecation chatter on stderr; keep output clean.
    warnings.simplefilter("ignore")
    cli()


if __name__ == "__main__":
    main()
