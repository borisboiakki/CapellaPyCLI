"""Model-editing operations.

Each operation takes the loaded model plus keyword arguments, mutates the
model in memory and returns a JSON-serializable result. Saving is left to
the caller so several operations can be applied atomically (see ``batch``).
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any

from . import capabilities as _capabilities
from . import chains as _chains
from . import data as _data
from . import interfaces as _interfaces
from . import modes as _modes
from . import physical as _physical
from . import status as _status
from . import structure as _structure
from .integrity import _check_structure_rules, delete, xml_type
from .model import (
    CHAIN_TYPES,
    COMPONENT_TYPE,
    FUNCTION_TYPE,
    CapError,
    brief,
    label,
    layer_key,
    remove_xml,
    resolve,
    root_component,
    root_function,
    type_name,
)
from .model import common_owner as _common_owner
from .model import is_component as _is_component
from .model import is_function as _is_function
from .model import require_layer as _require_layer
from .model import same_layer as _same_layer


def _expect(obj, pred, what: str):
    if not pred(obj):
        raise CapError(f"Expected {what}, got {type_name(obj)} {obj.uuid}")
    return obj


# --------------------------------------------------------------------- create


def create_function(model, parent: str, name: str, description: str | None = None):
    par = resolve(model, parent)
    key = _require_layer(par)
    if not hasattr(par, "functions"):
        raise CapError(f"{type_name(par)} cannot own functions")
    fn = par.functions.create(FUNCTION_TYPE[key], name=name)
    if description:
        fn.description = description
    return {"created": brief(fn), "parent": brief(par)}


def create_component(
    model,
    parent: str,
    name: str,
    actor: bool = False,
    nature: str | None = None,
    description: str | None = None,
):
    par = resolve(model, parent)
    key = _require_layer(par)
    _check_structure_rules(key, par, actor, root_component(model, key))
    if key == "oa":
        attr = "entities"
    elif key == "pa" and hasattr(par, "owned_components"):
        # PhysicalComponent.components is a computed view (owned + deployed).
        attr = "owned_components"
    else:
        attr = "components"
    if not hasattr(par, attr):
        raise CapError(f"{type_name(par)} cannot own {attr}")
    kw: dict[str, Any] = {"name": name}
    if actor:
        kw["is_actor"] = True
    if nature:
        if key != "pa":
            raise CapError("--nature only applies to physical components (pa)")
        kw["nature"] = nature.upper()
    comp = getattr(par, attr).create(COMPONENT_TYPE[key], **kw)
    if description:
        comp.description = description
    return {"created": brief(comp), "parent": brief(par)}


def create_function_exchange(model, source: str, target: str, name: str):
    src = _expect(resolve(model, source), _is_function, "a function/activity")
    tgt = _expect(resolve(model, target), _is_function, "a function/activity")
    key = _same_layer(src, tgt)
    owner = _common_owner(src, tgt, _is_function, root_function(model, key))
    if key == "oa":
        # Operational activities are connected directly, without ports.
        ex = owner.exchanges.create("FunctionalExchange", name=name, source=src, target=tgt)
        ports = []
    else:
        out = src.outputs.create("FunctionOutputPort", name=name)
        inp = tgt.inputs.create("FunctionInputPort", name=name)
        ex = owner.exchanges.create("FunctionalExchange", name=name, source=out, target=inp)
        ports = [brief(out), brief(inp)]
    return {"created": brief(ex), "owner": brief(owner), "ports": ports}


def create_component_exchange(
    model, source: str, target: str, name: str, kind: str | None = None
):
    src = _expect(resolve(model, source), _is_component, "a component/entity")
    tgt = _expect(resolve(model, target), _is_component, "a component/entity")
    key = _same_layer(src, tgt)
    owner = _common_owner(
        src, tgt, lambda x: hasattr(x, "component_exchanges"), root_component(model, key)
    )
    if key == "oa":
        coll = (
            owner.communication_means
            if hasattr(owner, "communication_means")
            else owner.component_exchanges
        )
        ex = coll.create("CommunicationMean", name=name, source=src, target=tgt)
        ports = []
    else:
        out = src.ports.create("ComponentPort", name=name, orientation="OUT")
        inp = tgt.ports.create("ComponentPort", name=name, orientation="IN")
        kw = {"kind": kind.upper()} if kind else {}
        ex = owner.component_exchanges.create(
            "ComponentExchange", name=name, source=out, target=inp, **kw
        )
        ports = [brief(out), brief(inp)]
    return {"created": brief(ex), "owner": brief(owner), "ports": ports}


# ----------------------------------------------------------------- relations


def _allocation_slot(elem, target):
    """Return (list attribute on target, label) for allocating elem to target."""
    te, tt = type_name(elem), type_name(target)
    if _is_function(elem) and _is_component(target):
        return "allocated_functions", "function->component"
    if te == "FunctionalExchange" and tt in ("ComponentExchange", "CommunicationMean"):
        return "allocated_functional_exchanges", "functional exchange->component exchange"
    if te == "ComponentExchange" and tt in ("PhysicalLink", "PhysicalPath"):
        return "allocated_component_exchanges", "component exchange->" + ("physical link" if tt == "PhysicalLink" else "physical path")
    if te == "ComponentPort" and tt == "PhysicalPort":
        return "allocated_component_ports", "component port->physical port"
    raise CapError(
        f"Don't know how to allocate {te} to {tt}; supported: function->component, "
        "functional exchange->component exchange, component exchange->physical link/path, "
        "component port->physical port"
    )


def allocate(model, element: str, to: str, move: bool = False):
    elem, target = resolve(model, element), resolve(model, to)
    _same_layer(elem, target)
    attr, relation = _allocation_slot(elem, target)
    if elem in getattr(target, attr):
        return {"unchanged": True, "reason": "already allocated", "element": brief(elem), "to": brief(target)}
    previous = []
    if attr == "allocated_functions":
        owner = getattr(elem, "owner", None)
        if owner is not None:
            if not move:
                raise CapError(
                    f"{label(elem)} is already allocated to {label(owner)}; "
                    "pass --move to re-allocate it"
                )
            getattr(owner, attr).remove(elem)
            previous.append(brief(owner))
    getattr(target, attr).append(elem)
    return {"allocated": relation, "element": brief(elem), "to": brief(target), "removed_from": previous}


def unallocate(model, element: str, from_: str):
    elem, target = resolve(model, element), resolve(model, from_)
    attr, _ = _allocation_slot(elem, target)
    if elem not in getattr(target, attr):
        raise CapError(f"{label(elem)} is not allocated to {label(target)}")
    getattr(target, attr).remove(elem)
    return {"unallocated": brief(elem), "from": brief(target)}




def realize(model, element: str, realized: str):
    """Trace ``element`` (lower layer) as realizing ``realized`` (layer above)."""
    elem, up = resolve(model, element), resolve(model, realized)
    if _is_function(elem) and _is_function(up):
        attr = "realized_functions"
    elif _is_component(elem) and _is_component(up):
        attr = "realized_components"
    elif type_name(elem) in CHAIN_TYPES and type_name(up) in CHAIN_TYPES:
        attr = "realized_chains"
    elif _capabilities.is_capability(elem) and _capabilities.is_capability(up):
        attr = "realized_capabilities"
    elif type_name(elem) in ("State", "Mode") and type_name(up) == type_name(elem):
        attr = "realized_states"
    elif type_name(elem) == type_name(up) == "StateTransition":
        attr = "realized_transitions"
    else:
        raise CapError(
            "realize links function->function, component->component, chain->chain, "
            "capability->capability, state->state, mode->mode or "
            "transition->transition"
        )
    order = ["oa", "sa", "la", "pa"]
    ke, ku = _require_layer(elem), _require_layer(up)
    if order.index(ke) != order.index(ku) + 1:
        raise CapError(f"{ke} elements realize elements of the layer directly above, not {ku}")
    if not hasattr(elem, attr):
        raise CapError(f"{type_name(elem)} has no {attr}")
    if up in getattr(elem, attr):
        return {"unchanged": True, "reason": "already realized"}
    getattr(elem, attr).append(up)
    _complete_trace_sources(elem)
    return {"element": brief(elem), "realizes": brief(up)}


def _realization_links(elem, up):
    """Realization links owned by ``elem`` that point at ``up``."""
    return [
        child
        for child in elem._element
        if isinstance(child.tag, str)
        and xml_type(child).endswith("Realization")
        and (child.get("targetElement") or "").endswith("#" + up.uuid)
    ]


def _complete_trace_sources(elem) -> int:
    """Fill in ``sourceElement`` on realization links owned by ``elem``.

    capellambse only writes ``targetElement``; Capella always writes both,
    and the source of a realization is the element that owns it.
    """
    fixed = 0
    for child in elem._element:
        if (
            isinstance(child.tag, str)
            and xml_type(child).endswith("Realization")
            and child.get("targetElement")
            and child.get("sourceElement") is None
        ):
            child.set("sourceElement", "#" + elem.uuid)
            fixed += 1
    return fixed


def unrealize(model, element: str, realized: str):
    """Remove the realization link from ``element`` to ``realized``."""
    elem, up = resolve(model, element), resolve(model, realized)
    links = _realization_links(elem, up)
    if not links:
        raise CapError(f"{label(elem)} does not realize {label(up)}")
    removed = []
    for link in links:
        removed.append({"uuid": link.get("id"), "type": xml_type(link)})
        remove_xml(model, link)
    return {"element": brief(elem), "no_longer_realizes": brief(up), "removed": removed}


# -------------------------------------------------------------------- update

_SETTABLE_SCALARS = (str, bool, int, float)


def set_attrs(model, element: str, values: dict[str, Any]):
    obj = resolve(model, element)
    changed = {}
    for key, raw in values.items():
        if key in ("uuid", "xtype", "parent"):
            raise CapError(f"{key!r} cannot be set")
        if key in ("status", "progress_status"):
            # Restricted to the project's ProgressStatus values (see status.py).
            res = _status.set_status(model, raw, [obj.uuid])
            changed[key] = res["status"]
            continue
        if key == "is_actor" and _is_component(obj) and layer_key(obj) != "oa":
            raise CapError(
                "is_actor cannot be changed in place: actors and components live in "
                "different places (Structure package vs. inside the system). Delete "
                "and recreate the element with `create component [--actor]`"
            )
        try:
            current = getattr(obj, key)
        except AttributeError:
            raise CapError(f"{type_name(obj)} has no attribute {key!r}") from None
        if hasattr(current, "uuid") or hasattr(current, "by_uuid"):
            raise CapError(
                f"{key!r} is a reference; use allocate/realize/create commands instead"
            )
        value: Any = raw
        # Values come as strings from the command line, or as JSON values
        # (true, 42, 1.5) from batch: accept both.
        text = str(raw).lower() if isinstance(raw, bool) else str(raw)
        if isinstance(current, bool):
            if text.lower() not in ("true", "false"):
                raise CapError(f"{key!r} expects true or false, got {raw!r}")
            value = text.lower() == "true"
        elif isinstance(current, (int, float)):
            kind = int if isinstance(current, int) else float
            try:
                value = kind(text)
            except ValueError:
                raise CapError(f"{key!r} expects {'an integer' if kind is int else 'a number'}, got {raw!r}") from None
        elif hasattr(current, "name") and hasattr(current, "value"):  # enum
            allowed = [m.name for m in type(current)]
            value = text.upper()
            if value not in allowed:
                raise CapError(f"{key!r} must be one of {', '.join(allowed)}")
        elif current is not None and not isinstance(current, _SETTABLE_SCALARS):
            # Markup (description) and other str-likes are fine; refuse the rest.
            if not isinstance(current, str):
                raise CapError(f"{key!r} has unsupported type {type(current).__name__}")
        if isinstance(value, (int, float)) and not isinstance(current, (bool, int, float)):
            value = text  # e.g. name: 42 in a batch
        try:
            setattr(obj, key, value)
        except Exception as e:  # noqa: BLE001
            raise CapError(f"Cannot set {key!r}: {e}") from None
        changed[key] = str(getattr(obj, key))
    return {"updated": brief(obj), "values": changed}


# --------------------------------------------------------------------- batch

OPS: dict[str, Callable[..., Any]] = {
    "create-function": create_function,
    "create-component": create_component,
    "create-function-exchange": create_function_exchange,
    "create-component-exchange": create_component_exchange,
    "allocate": allocate,
    "unallocate": unallocate,
    "realize": realize,
    "unrealize": unrealize,
    "set": set_attrs,
    "delete": delete,
}


# Only whole strings like "$f1" are aliases: "$5 budget" is plain text.
_ALIAS_REF = re.compile(r"\$[A-Za-z_][\w-]*")


def run_batch(model, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run several operations; ``"$alias"`` refers to an earlier step's result.

    A step looks like ``{"op": "create-function", "as": "f1",
    "parent": "la:root-function", "name": "Compute route"}``.
    """
    aliases: dict[str, str] = {}
    results = []
    if not isinstance(steps, list):
        raise CapError("batch input must be a JSON list of steps")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise CapError(f"step {i}: expected an object like {{\"op\": ..., ...}}, got {type(step).__name__}")
        step = dict(step)
        op = step.pop("op", None)
        alias = step.pop("as", None)
        if op not in OPS:
            raise CapError(f"step {i}: unknown op {op!r}; known: {', '.join(OPS)}")

        def sub(v, i=i):
            if isinstance(v, str) and v.startswith("$$"):
                return v[1:]  # escaped literal "$..."
            if isinstance(v, str) and _ALIAS_REF.fullmatch(v):
                if v[1:] not in aliases:
                    raise CapError(f"step {i}: unknown alias {v} (write $$ for a literal $)")
                return aliases[v[1:]]
            if isinstance(v, dict):
                return {k: sub(x) for k, x in v.items()}
            if isinstance(v, list):
                return [sub(x) for x in v]
            return v

        kwargs = {k.replace("-", "_"): sub(v) for k, v in step.items()}
        if op == "unallocate" and "from" in kwargs:
            kwargs["from_"] = kwargs.pop("from")
        if op == "add-property" and "class" in kwargs:
            kwargs["cls"] = kwargs.pop("class")  # `class` is a Python keyword
        if "layer" in kwargs and "layer_name" in inspect.signature(OPS[op]).parameters:
            kwargs["layer_name"] = kwargs.pop("layer")  # every create op taking a layer
        if isinstance(kwargs.get("layer_name"), str):
            kwargs["layer_name"] = kwargs["layer_name"].lower()  # "LA" works like on the CLI
        params = inspect.signature(OPS[op])
        try:
            params.bind(model, **kwargs)
        except TypeError as e:
            known = ", ".join(p for p in list(params.parameters)[1:])
            raise CapError(f"step {i} ({op}): bad arguments: {e}; {op} takes: {known}") from None
        try:
            res = OPS[op](model, **kwargs)
        except CapError as e:
            raise CapError(f"step {i} ({op}): {e}") from None
        if alias:
            created = res.get("created")
            if not created:
                raise CapError(f"step {i}: 'as' is only valid on create-* ops")
            aliases[alias] = created["uuid"]
        results.append({"op": op, **({"as": alias} if alias else {}), **res})
    return results

OPS.update(_chains.OPS)
OPS.update(_capabilities.OPS)
OPS.update(_status.OPS)
OPS.update(_data.OPS)
OPS.update(_modes.OPS)
OPS.update(_physical.OPS)
OPS.update(_structure.OPS)
OPS.update(_interfaces.OPS)
