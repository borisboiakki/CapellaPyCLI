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

from . import __version__, capabilities, chains, data, interfaces, modes, ops, physical, status, structure
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
    if capabilities.is_capability(obj) and not attrs:
        emit(capabilities.show_capability(ctx.model, uuid))
        return
    if type_name(obj) == "Interface" and not attrs:
        emit(interfaces.show(ctx.model, uuid))
        return
    if physical.is_physical_element(obj) and not attrs:
        emit(physical.show(ctx.model, uuid))
        return
    if modes.is_mode_element(obj) and not attrs:
        emit(modes.show(ctx.model, uuid))
        return
    if data.is_data_element(obj) and not attrs:
        emit(data.show(ctx.model, uuid))
        return
    if type_name(obj) == "Mission" and not attrs:
        emit(capabilities.show_mission(ctx.model, uuid))
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
        # Requirements keep their name in ReqIFLongName rather than `name`,
        # so fall back to capellambse's accessor; unnamed elements must still
        # show up in a pure --type search.
        name = el._element.get("name")
        if name is None:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    name = getattr(el, "name", "")
            except Exception:  # noqa: BLE001
                name = ""
        name = name if isinstance(name, str) else ""
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
@click.option("--fix", is_flag=True, help="Fill in missing sourceElement on realization links and save.")
def check(ctx: Ctx, fix: bool) -> None:
    """Check the model for dangling, empty or incomplete references (run after edits)."""
    res = ops.check(ctx.model, fix=fix)
    if fix and res.get("fixed"):
        res["saved"] = ctx.save()
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

    Works for functions, components, functional chains and capabilities
    (e.g. LA fn -> SA fn, LA capability realization -> SA capability).
    """
    return ops.realize(model, element, realized)


@cli.command()
@click.argument("element")
@click.argument("realized")
@write_command
def unrealize(model, element, realized):
    """Remove the realization link from ELEMENT to REALIZED."""
    return ops.unrealize(model, element, realized)


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


@cli.group()
def capability() -> None:
    """Capabilities (operational capabilities in OA, capability realizations in LA/PA)."""


@capability.command("list")
@click.argument("layer_name", metavar="LAYER", type=click.Choice(LAYERS))
@click.option("--involving", help="Only capabilities involving this element UUID.")
@click.pass_obj
@handled
def capability_list(ctx: Ctx, layer_name: str, involving: str | None) -> None:
    """List the capabilities of a layer with their number of issues."""
    m = ctx.model
    items = list(layer(m, layer_name).all_capabilities)
    if involving:
        uuid = resolve(m, involving).uuid
        items = [
            c for c in items
            if any(x["uuid"] == uuid for group in capabilities.involved(c).values() for x in group)
        ]
    emit({
        "count": len(items),
        "items": [{**brief(c), "issues": len(capabilities.show_capability(m, c.uuid)["issues"])} for c in items],
    })


@capability.command("show")
@click.argument("uuid")
@click.pass_obj
@handled
def capability_show(ctx: Ctx, uuid: str) -> None:
    """Show involvements, realizations, relations, missions and issues."""
    emit(capabilities.show_capability(ctx.model, uuid))


@capability.command("create")
@click.option("--name", required=True)
@click.option("--layer", "layer_name", type=click.Choice(LAYERS), help="Create in the layer's capability package.")
@click.option("--parent", help="Capability package UUID (instead of --layer).")
@click.option("--description")
@write_command
def capability_create(model, name, layer_name, parent, description):
    """Create a capability with the right metaclass for the layer."""
    return capabilities.create_capability(model, name, layer_name, parent, description)


@capability.command("involve")
@click.argument("capability_uuid", metavar="CAPABILITY")
@click.argument("elements", nargs=-1, required=True)
@write_command
def capability_involve(model, capability_uuid, elements):
    """Involve components/actors (entities in OA), functions or chains."""
    return capabilities.involve(model, capability_uuid, list(elements))


@capability.command("uninvolve")
@click.argument("capability_uuid", metavar="CAPABILITY")
@click.argument("elements", nargs=-1, required=True)
@write_command
def capability_uninvolve(model, capability_uuid, elements):
    """Remove involvements; the elements themselves are kept."""
    return capabilities.uninvolve(model, capability_uuid, list(elements))


def _relation_command(relation: str, verb: str):
    @capability.command(relation, help=f"CAPABILITY {verb} OTHER (same layer). --remove deletes the link.")
    @click.argument("capability_uuid", metavar="CAPABILITY")
    @click.argument("other", metavar="OTHER")
    @click.option("--remove", is_flag=True)
    @write_command
    def _cmd(model, capability_uuid, other, remove):
        return capabilities.relate(model, relation, capability_uuid, other, remove)

    return _cmd


_relation_command("include", "includes")
_relation_command("extend", "extends")
_relation_command("generalize", "specializes (OTHER is the more general capability)")


@cli.group("data")
def data_group() -> None:
    """Data model: classes, enumerations, exchange items and their use."""


@data_group.command("types")
@click.option("--layer", "layer_name", type=click.Choice(LAYERS), required=True)
@click.option("--name", "name_filter", help="Case-insensitive substring filter.")
@click.pass_obj
@handled
def data_types(ctx: Ctx, layer_name: str, name_filter: str | None) -> None:
    """Types usable from LAYER: its own, the layers above, and the predefined ones."""
    emit(data.types(ctx.model, layer_name, name_filter))


@data_group.command("show")
@click.argument("uuid")
@click.pass_obj
@handled
def data_show(ctx: Ctx, uuid: str) -> None:
    """Show a class, enumeration, exchange item or data type with its usage and issues."""
    emit(data.show(ctx.model, uuid))


@data_group.group("class")
def data_class() -> None:
    """Classes and their properties."""


@data_class.command("create")
@click.option("--name", required=True)
@click.option("--layer", "layer_name", type=click.Choice(LAYERS), help="Create in the layer's data package.")
@click.option("--parent", help="Data package UUID (instead of --layer).")
@click.option("--description")
@write_command
def data_class_create(model, name, layer_name, parent, description):
    """Create a class in a data package."""
    return data.create_class(model, name, layer_name, parent, description)


@data_group.group("property")
def data_property() -> None:
    """Class properties."""


@data_property.command("add")
@click.argument("class_uuid", metavar="CLASS")
@click.option("--name", required=True)
@click.option("--type", "type_", required=True, help="Type UUID (see `capcli data types`).")
@click.option("--min", "min_", default="1", show_default=True)
@click.option("--max", "max_", default="1", show_default=True, help="A number or '*'.")
@click.option("--kind", type=click.Choice(["association", "aggregation", "composition"], case_sensitive=False),
              default="association", show_default=True)
@click.option("--description")
@write_command
def data_property_add(model, class_uuid, name, type_, min_, max_, kind, description):
    """Add a typed property with a multiplicity to CLASS."""
    return data.add_property(model, class_uuid, name, type_, min_, max_, kind, description)


@data_group.group("enum")
def data_enum() -> None:
    """Enumerations and their literals."""


@data_enum.command("create")
@click.option("--name", required=True)
@click.option("--layer", "layer_name", type=click.Choice(LAYERS))
@click.option("--parent", help="Data package UUID (instead of --layer).")
@click.option("--literal", "literals", multiple=True, help="Literal name (repeatable, in order).")
@click.option("--description")
@write_command
def data_enum_create(model, name, layer_name, parent, literals, description):
    """Create an enumeration, optionally with its literals."""
    return data.create_enumeration(model, name, layer_name, parent, list(literals), description)


@data_enum.command("add-literal")
@click.argument("enum_uuid", metavar="ENUMERATION")
@click.argument("literals", nargs=-1, required=True)
@write_command
def data_enum_add_literal(model, enum_uuid, literals):
    """Append literals to an enumeration."""
    return data.add_literals(model, enum_uuid, list(literals))


@data_group.group("exchange-item")
def data_exchange_item() -> None:
    """Exchange items and their elements."""


@data_exchange_item.command("create")
@click.option("--name", required=True)
@click.option("--layer", "layer_name", type=click.Choice(LAYERS))
@click.option("--parent", help="Data package UUID (instead of --layer).")
@click.option("--mechanism", type=click.Choice(["unset", "flow", "operation", "event", "shared_data"], case_sensitive=False),
              default="unset", show_default=True)
@click.option("--description")
@write_command
def data_exchange_item_create(model, name, layer_name, parent, mechanism, description):
    """Create an exchange item."""
    return data.create_exchange_item(model, name, layer_name, parent, mechanism, description)


@data_exchange_item.command("add-element")
@click.argument("exchange_item", metavar="EXCHANGE_ITEM")
@click.option("--name", required=True)
@click.option("--type", "type_", required=True, help="Type UUID (see `capcli data types`).")
@click.option("--min", "min_", default="1", show_default=True)
@click.option("--max", "max_", default="1", show_default=True)
@write_command
def data_exchange_item_add_element(model, exchange_item, name, type_, min_, max_):
    """Add a typed element to an exchange item."""
    return data.add_element(model, exchange_item, name, type_, min_, max_)


@data_group.command("assign")
@click.argument("exchange_item", metavar="EXCHANGE_ITEM")
@click.argument("elements", nargs=-1, required=True)
@click.option("--remove", is_flag=True)
@write_command
def data_assign(model, exchange_item, elements, remove):
    """Make functional exchanges, function ports or component exchanges carry EXCHANGE_ITEM."""
    return data.assign(model, exchange_item, list(elements), remove)


@cli.group("mode")
def mode_group() -> None:
    """Modes and states: state machines, states/modes, transitions, availability."""


@mode_group.command("list")
@click.argument("layer_name", metavar="LAYER", type=click.Choice(LAYERS))
@click.pass_obj
@handled
def mode_list(ctx: Ctx, layer_name: str) -> None:
    """List the state machines of a layer with their owner."""
    emit(modes.list_machines(ctx.model, layer_name))


@mode_group.command("show")
@click.argument("uuid")
@click.pass_obj
@handled
def mode_show(ctx: Ctx, uuid: str) -> None:
    """Show a state machine (tree of regions, states, transitions) or a state."""
    emit(modes.show(ctx.model, uuid))


@mode_group.group("machine")
def mode_machine() -> None:
    """State machines."""


@mode_machine.command("create")
@click.argument("owner")
@click.option("--name", help='Default: "<owner> State Machine".')
@write_command
def mode_machine_create(model, owner, name):
    """Create a state machine (with its Default Region) on a component, actor or entity."""
    return modes.create_machine(model, owner, name)


@mode_group.command("add")
@click.argument("parent")
@click.option("--name", required=True)
@click.option("--kind", type=click.Choice(list(modes.KINDS), case_sensitive=False), default="state", show_default=True)
@click.option("--description")
@write_command
def mode_add(model, parent, name, kind, description):
    """Add a state, mode or pseudo-state to a state machine, region or state (sub-state)."""
    return modes.add_state(model, parent, name, kind, description)


@mode_group.command("transition")
@click.argument("source")
@click.argument("target")
@click.option("--trigger", "triggers", multiple=True, help="Functional/component exchange or exchange item (repeatable).")
@click.option("--effect", "effects", multiple=True, help="Function run on the transition (repeatable).")
@click.option("--trigger-description", help="Free-text trigger.")
@click.option("--guard", help="Guard condition text.")
@click.option("--name")
@write_command
def mode_transition(model, source, target, triggers, effects, trigger_description, guard, name):
    """Add a transition between two states/modes of the same state machine."""
    return modes.add_transition(model, source, target, list(triggers), list(effects),
                                trigger_description, guard, name)


@mode_group.command("available")
@click.argument("state")
@click.argument("elements", nargs=-1, required=True)
@click.option("--remove", is_flag=True)
@write_command
def mode_available(model, state, elements, remove):
    """Declare functions, chains or capabilities available in STATE (a state or mode)."""
    return modes.set_available(model, state, list(elements), remove)


@cli.group("pa")
def pa_group() -> None:
    """Physical architecture: physical ports, links, paths and deployment."""


@pa_group.command("list")
@click.argument("what", type=click.Choice(["nodes", "behaviors", "links", "paths"]))
@click.pass_obj
@handled
def pa_list(ctx: Ctx, what: str) -> None:
    """List node or behaviour components (with their host), links or paths."""
    emit(physical.list_physical(ctx.model, what))


@pa_group.command("show")
@click.argument("uuid")
@click.pass_obj
@handled
def pa_show(ctx: Ctx, uuid: str) -> None:
    """Show a physical component, port, link or path with deployment and issues."""
    emit(physical.show(ctx.model, uuid))


@pa_group.command("port")
@click.argument("component")
@click.option("--name", required=True)
@write_command
def pa_port(model, component, name):
    """Add a physical port to a node component."""
    return physical.create_port(model, component, name)


@pa_group.command("link")
@click.option("--source", required=True, help="Node component or physical port.")
@click.option("--target", required=True, help="Node component or physical port.")
@click.option("--name", required=True)
@write_command
def pa_link(model, source, target, name):
    """Link two nodes; physical ports are created when components are given."""
    return physical.create_link(model, source, target, name)


@pa_group.command("path")
@click.option("--name", required=True)
@click.option("--link", "links", multiple=True, required=True, help="Physical link UUIDs in order (repeat).")
@click.option("--parent", help="Physical component owning the path (default: the physical system).")
@write_command
def pa_path(model, name, links, parent):
    """Create a physical path through consecutive links (nodes are derived)."""
    return physical.create_path(model, name, list(links), parent)


@pa_group.command("deploy")
@click.argument("element")
@click.argument("host")
@click.option("--remove", is_flag=True)
@write_command
def pa_deploy(model, element, host, remove):
    """Deploy ELEMENT (a behaviour or node component) on HOST."""
    return physical.deploy(model, element, host, remove)


@cli.group("interface")
def interface_group() -> None:
    """Interfaces: exchange items, provided/required, allocation."""


@interface_group.command("list")
@click.argument("layer_name", metavar="LAYER", type=click.Choice(LAYERS))
@click.pass_obj
@handled
def interface_list(ctx: Ctx, layer_name: str) -> None:
    """List the interfaces of a layer."""
    emit(interfaces.list_interfaces(ctx.model, layer_name))


@interface_group.command("show")
@click.argument("uuid")
@click.pass_obj
@handled
def interface_show(ctx: Ctx, uuid: str) -> None:
    """Show an interface: items, providers, requirers, allocations, issues."""
    emit(interfaces.show(ctx.model, uuid))


@interface_group.command("create")
@click.option("--name", required=True)
@click.option("--layer", "layer_name", type=click.Choice(LAYERS))
@click.option("--parent", help="Interface package UUID (instead of --layer).")
@click.option("--description")
@write_command
def interface_create(model, name, layer_name, parent, description):
    """Create an interface in an interface package."""
    return interfaces.create_interface(model, name, layer_name, parent, description)


@interface_group.command("items")
@click.argument("interface")
@click.argument("elements", nargs=-1, required=True)
@click.option("--remove", is_flag=True)
@write_command
def interface_items(model, interface, elements, remove):
    """Add (or --remove) exchange items carried by INTERFACE."""
    return interfaces.set_items(model, interface, list(elements), remove)


@interface_group.command("provide")
@click.argument("element")
@click.argument("interface")
@click.option("--remove", is_flag=True)
@write_command
def interface_provide(model, element, interface, remove):
    """ELEMENT (component or component port) provides INTERFACE."""
    return interfaces.provide(model, element, interface, remove)


@interface_group.command("require")
@click.argument("element")
@click.argument("interface")
@click.option("--remove", is_flag=True)
@write_command
def interface_require(model, element, interface, remove):
    """ELEMENT (component or component port) requires INTERFACE."""
    return interfaces.require(model, element, interface, remove)


@interface_group.command("allocate")
@click.argument("element")
@click.argument("interface")
@click.option("--remove", is_flag=True)
@write_command
def interface_allocate(model, element, interface, remove):
    """Allocate INTERFACE to ELEMENT (a component or another interface)."""
    return interfaces.allocate_interface(model, element, interface, remove)


@cli.group("package")
def package_group() -> None:
    """Packages (function, component, capability, data and interface packages)."""


@package_group.command("create")
@click.option("--parent", required=True, help="A package, function or component; or la:functions, la:structure, la:capabilities, la:data, la:interfaces.")
@click.option("--name", required=True)
@write_command
def package_create(model, parent, name):
    """Create a sub-package; its kind follows from PARENT."""
    return structure.create_package(model, parent, name)


@cli.command("move")
@click.argument("element")
@click.argument("to")
@write_command
def move_cmd(model, element, to):
    """Move ELEMENT under TO (same layer); its Part and exchanges follow."""
    return structure.move(model, element, to)


@cli.group("repair")
def repair_group() -> None:
    """Repair models (preview with `capcli --dry-run repair …`)."""


@repair_group.command("structure")
@write_command
def repair_structure(model):
    """Move actors out of components into the Structure package; report SA sub-systems."""
    return structure.repair(model)


@cli.group("status")
def status_group() -> None:
    """Progress status of elements (DRAFT, TO_BE_REVIEWED, REVIEWED_OK, …)."""


@status_group.command("values")
@click.pass_obj
@handled
def status_values(ctx: Ctx) -> None:
    """List the status values defined in the project."""
    emit({"values": status.values(ctx.model), "clear_with": status.NOT_SET})


@status_group.command("set")
@click.argument("value")
@click.argument("elements", nargs=-1, required=True)
@write_command
def status_set(model, value, elements):
    """Set VALUE on one or more elements (NOT_SET clears the status)."""
    return status.set_status(model, value, list(elements))


@status_group.command("list")
@click.argument("value", required=False)
@click.option("--layer", "layer_name", type=click.Choice(LAYERS))
@click.pass_obj
@handled
def status_list(ctx: Ctx, value: str | None, layer_name: str | None) -> None:
    """List elements that have a status, grouped by value (optionally only VALUE)."""
    emit(status.list_by_status(ctx.model, value, layer_name))


@cli.group()
def mission() -> None:
    """Missions (System Analysis) and the capabilities they exploit."""


@mission.command("create")
@click.option("--name", required=True)
@click.option("--description")
@write_command
def mission_create(model, name, description):
    """Create a mission in the System Analysis mission package."""
    return capabilities.create_mission(model, name, description)


@mission.command("show")
@click.argument("uuid")
@click.pass_obj
@handled
def mission_show(ctx: Ctx, uuid: str) -> None:
    """Show the capabilities a mission exploits and the actors it involves."""
    emit(capabilities.show_mission(ctx.model, uuid))


@mission.command("exploit")
@click.argument("mission_uuid", metavar="MISSION")
@click.argument("capability_uuid", metavar="CAPABILITY")
@click.option("--remove", is_flag=True)
@write_command
def mission_exploit(model, mission_uuid, capability_uuid, remove):
    """Record that MISSION exploits an SA CAPABILITY."""
    return capabilities.mission_exploit(model, mission_uuid, capability_uuid, remove)


@mission.command("involve")
@click.argument("mission_uuid", metavar="MISSION")
@click.argument("elements", nargs=-1, required=True)
@click.option("--remove", is_flag=True)
@write_command
def mission_involve(model, mission_uuid, elements, remove):
    """Involve SA actors (or the system) in MISSION."""
    return capabilities.mission_involve(model, mission_uuid, list(elements), remove)


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
