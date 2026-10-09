"""Model-editing operations.

Each operation takes the loaded model plus keyword arguments, mutates the
model in memory and returns a JSON-serializable result. Saving is left to
the caller so several operations can be applied atomically (see ``batch``).
"""

from __future__ import annotations

import re
from typing import Any, Callable

from capellambse import loader as _loader
from lxml import etree

from . import chains as _chains
from .model import (
    COMPONENT_TYPE,
    FUNCTION_TYPE,
    CapError,
    brief,
    layer_key,
    resolve,
    root_component,
    root_function,
    type_name,
)
from .model import is_component as _is_component
from .model import is_function as _is_function
from .model import require_layer as _require_layer
from .model import same_layer as _same_layer


def _expect(obj, pred, what: str):
    if not pred(obj):
        raise CapError(f"Expected {what}, got {type_name(obj)} {obj.uuid}")
    return obj


def _ancestors(obj):
    p = getattr(obj, "parent", None)
    while p is not None and layer_key(p) is not None:
        yield p
        p = getattr(p, "parent", None)


def _common_owner(a, b, pred, fallback):
    """Deepest common proper ancestor of a and b satisfying pred."""
    b_anc = {x.uuid for x in _ancestors(b)}
    for x in _ancestors(a):
        if pred(x) and x.uuid in b_anc:
            return x
    return fallback


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
    raise CapError(
        f"Don't know how to allocate {te} to {tt}; supported: function->component, "
        "functional exchange->component exchange"
    )


def allocate(model, element: str, to: str, move: bool = False):
    elem, target = resolve(model, element), resolve(model, to)
    _same_layer(elem, target)
    attr, label = _allocation_slot(elem, target)
    if elem in getattr(target, attr):
        return {"unchanged": True, "reason": "already allocated", "element": brief(elem), "to": brief(target)}
    previous = []
    if attr == "allocated_functions":
        owner = getattr(elem, "owner", None)
        if owner is not None:
            if not move:
                raise CapError(
                    f"{brief(elem)} is already allocated to {brief(owner)}; "
                    "pass --move to re-allocate it"
                )
            getattr(owner, attr).remove(elem)
            previous.append(brief(owner))
    getattr(target, attr).append(elem)
    return {"allocated": label, "element": brief(elem), "to": brief(target), "removed_from": previous}


def unallocate(model, element: str, from_: str):
    elem, target = resolve(model, element), resolve(model, from_)
    attr, _ = _allocation_slot(elem, target)
    if elem not in getattr(target, attr):
        raise CapError(f"{brief(elem)} is not allocated to {brief(target)}")
    getattr(target, attr).remove(elem)
    return {"unallocated": brief(elem), "from": brief(target)}


_CHAIN_TYPES = ("FunctionalChain", "OperationalProcess")


def realize(model, element: str, realized: str):
    """Trace ``element`` (lower layer) as realizing ``realized`` (layer above)."""
    elem, up = resolve(model, element), resolve(model, realized)
    if _is_function(elem) and _is_function(up):
        attr = "realized_functions"
    elif _is_component(elem) and _is_component(up):
        attr = "realized_components"
    elif type_name(elem) in _CHAIN_TYPES and type_name(up) in _CHAIN_TYPES:
        attr = "realized_chains"
    else:
        raise CapError(
            "realize links function->function, component->component or chain->chain"
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
    return {"element": brief(elem), "realizes": brief(up)}


# -------------------------------------------------------------------- update

_SETTABLE_SCALARS = (str, bool, int, float)


def set_attrs(model, element: str, values: dict[str, str]):
    obj = resolve(model, element)
    changed = {}
    for key, raw in values.items():
        if key in ("uuid", "xtype", "parent"):
            raise CapError(f"{key!r} cannot be set")
        try:
            current = getattr(obj, key)
        except AttributeError:
            raise CapError(f"{type_name(obj)} has no attribute {key!r}") from None
        if hasattr(current, "uuid") or hasattr(current, "by_uuid"):
            raise CapError(
                f"{key!r} is a reference; use allocate/realize/create commands instead"
            )
        value: Any = raw
        if isinstance(current, bool):
            if raw.lower() not in ("true", "false"):
                raise CapError(f"{key!r} expects true/false")
            value = raw.lower() == "true"
        elif isinstance(current, int):
            value = int(raw)
        elif isinstance(current, float):
            value = float(raw)
        elif hasattr(current, "name") and hasattr(current, "value"):  # enum
            allowed = [m.name for m in type(current)]
            value = raw.upper()
            if value not in allowed:
                raise CapError(f"{key!r} must be one of {', '.join(allowed)}")
        elif current is not None and not isinstance(current, _SETTABLE_SCALARS):
            # Markup (description) and other str-likes are fine; refuse the rest.
            if not isinstance(current, str):
                raise CapError(f"{key!r} has unsupported type {type(current).__name__}")
        try:
            setattr(obj, key, value)
        except Exception as e:  # noqa: BLE001
            raise CapError(f"Cannot set {key!r}: {e}") from None
        changed[key] = str(getattr(obj, key))
    return {"updated": brief(obj), "values": changed}


# -------------------------------------------------------------------- delete

# Relationship elements that are meaningless once one of their ends is gone
# and can therefore be removed together with it (``delete --cascade``).
_CASCADABLE = re.compile(
    r"(Exchange|CommunicationMean|Allocation|Realization|Involvement\w*|"
    r"Link|Part|Port|Trace|Generalization)$"
)
_EXCHANGE = re.compile(r"(Exchange|CommunicationMean)$")
_REF_TOKEN = re.compile(r"#([A-Za-z0-9_-]+)$")
_NON_REF_ATTRS = {"id", "name", "description", "summary", "review", "sid"}


def _xtype(elem) -> str:
    xt = elem.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
    return xt.rpartition(":")[2] or etree.QName(elem).localname


def _semantic_roots(model):
    return [
        t.root
        for t in model._loader.trees.values()
        if t.fragment_type == _loader.FragmentType.SEMANTIC
    ]


def iter_refs(model, visual: bool = False):
    """Yield (element, attribute, referenced id) for every XML reference."""
    roots = _semantic_roots(model)
    if visual:
        roots = [
            t.root
            for t in model._loader.trees.values()
            if t.fragment_type == _loader.FragmentType.VISUAL
        ]
    for root in roots:
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for attr, value in el.attrib.items():
                if attr in _NON_REF_ATTRS or "#" not in value:
                    continue
                tokens = value.split()
                ids = [m.group(1) for t in tokens if (m := _REF_TOKEN.search(t))]
                if len(ids) != len(tokens):
                    continue  # free text that happens to contain '#'
                for i in ids:
                    yield el, attr, i


def _owning_element(el):
    while el is not None and el.get("id") is None:
        el = el.getparent()
    return el


def delete(model, element: str, cascade: bool = False):
    obj = resolve(model, element)
    if layer_key(obj) is None or obj._element.getparent() is None:
        raise CapError("Refusing to delete a layer or model root")
    key = layer_key(obj)
    if type_name(obj).endswith("Pkg") or obj in (
        root_function(model, key),
        getattr(obj.layer, "root_component", None),
    ):
        raise CapError("Refusing to delete a package or a layer's root function/component")

    doomed = {obj._element}

    def doomed_ids():
        return {
            e.get("id") for d in doomed for e in d.iter() if isinstance(e.tag, str) and e.get("id")
        }

    def inside_doomed(el):
        return any(a in doomed for a in [el, *el.iterancestors()])

    # Fixed point: cascading an exchange may orphan its allocation, and so on.
    while True:
        ids = doomed_ids()
        blockers, extra = [], []
        for el, attr, ref in iter_refs(model):
            if ref not in ids or inside_doomed(el):
                continue
            owner = _owning_element(el)
            info = {"uuid": owner.get("id"), "type": _xtype(owner), "name": owner.get("name"), "via": attr}
            if cascade and _CASCADABLE.search(info["type"]):
                extra.append(owner)
            else:
                blockers.append(info)
        if blockers:
            raise CapError(
                "Element is still referenced; "
                + ("these references cannot be cascaded: " if cascade else "re-run with --cascade or remove them first: ")
                + "; ".join(f"{b['type']} {b['name']!r} {b['uuid']} (via {b['via']})" for b in _dedupe(blockers))
            )
        new = [e for e in extra if e not in doomed]
        if not new:
            break
        doomed.update(new)

    # Ports that only existed to carry a deleted exchange are removed too.
    port_ids = {
        e.get(a)[1:]
        for d in doomed
        for e in d.iter()
        if isinstance(e.tag, str) and _EXCHANGE.search(_xtype(e))
        for a in ("source", "target")
        if (e.get(a) or "").startswith("#")
    }

    ids = doomed_ids()
    diagram_refs = {ref for _, _, ref in iter_refs(model, visual=True) if ref in ids}
    removed = []
    for el in doomed:
        if any(a in doomed for a in el.iterancestors()):
            continue
        removed.append({"uuid": el.get("id"), "type": _xtype(el), "name": el.get("name")})
        model._loader.idcache_remove(el)
        el.getparent().remove(el)

    still_used = {ref for _, _, ref in iter_refs(model)}
    for pid in port_ids - ids - still_used:
        try:
            port = model._loader[pid]
        except KeyError:
            continue
        if _xtype(port).endswith("Port") and port.getparent() is not None:
            removed.append({"uuid": pid, "type": _xtype(port), "name": port.get("name")})
            model._loader.idcache_remove(port)
            port.getparent().remove(port)

    result: dict[str, Any] = {"deleted": removed}
    if diagram_refs:
        result["warning"] = (
            f"{len(diagram_refs)} deleted element(s) appear on diagrams; open the "
            "model in Capella and refresh/clean the affected diagrams"
        )
    return result


def _dedupe(items):
    seen, out = set(), []
    for i in items:
        if i["uuid"] not in seen:
            seen.add(i["uuid"])
            out.append(i)
    return out


# --------------------------------------------------------------------- check

_REQUIRED_REF_ATTRS = ("source", "target", "sourceElement", "targetElement", "abstractType")


def check(model) -> dict[str, Any]:
    """Look for dangling or empty references in the semantic model."""
    known = set()
    for t in model._loader.trees.values():
        for el in t.root.iter():
            if isinstance(el.tag, str) and el.get("id"):
                known.add(el.get("id"))
    dangling = []
    for el, attr, ref in iter_refs(model):
        if ref not in known:
            owner = _owning_element(el)
            dangling.append({"uuid": owner.get("id"), "type": _xtype(owner), "attr": attr, "missing": ref})
    empty = []
    for root in _semantic_roots(model):
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for attr in _REQUIRED_REF_ATTRS:
                if el.get(attr) == "":
                    empty.append({"uuid": el.get("id"), "type": _xtype(el), "attr": attr})
    return {"ok": not dangling and not empty, "dangling": dangling, "empty": empty}


# --------------------------------------------------------------------- batch

OPS: dict[str, Callable[..., dict[str, Any]]] = {
    "create-function": create_function,
    "create-component": create_component,
    "create-function-exchange": create_function_exchange,
    "create-component-exchange": create_component_exchange,
    "allocate": allocate,
    "unallocate": unallocate,
    "realize": realize,
    "set": set_attrs,
    "delete": delete,
}


def run_batch(model, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run several operations; ``"$alias"`` refers to an earlier step's result.

    A step looks like ``{"op": "create-function", "as": "f1",
    "parent": "la:root-function", "name": "Compute route"}``.
    """
    aliases: dict[str, str] = {}
    results = []
    for i, step in enumerate(steps):
        step = dict(step)
        op = step.pop("op", None)
        alias = step.pop("as", None)
        if op not in OPS:
            raise CapError(f"step {i}: unknown op {op!r}; known: {', '.join(OPS)}")

        def sub(v):
            if isinstance(v, str) and v.startswith("$"):
                if v[1:] not in aliases:
                    raise CapError(f"step {i}: unknown alias {v}")
                return aliases[v[1:]]
            if isinstance(v, dict):
                return {k: sub(x) for k, x in v.items()}
            if isinstance(v, list):
                return [sub(x) for x in v]
            return v

        kwargs = {k.replace("-", "_"): sub(v) for k, v in step.items()}
        if op == "unallocate" and "from" in kwargs:
            kwargs["from_"] = kwargs.pop("from")
        if op == "create-chain" and "layer" in kwargs:
            kwargs["layer_name"] = kwargs.pop("layer")
        try:
            res = OPS[op](model, **kwargs)
        except CapError as e:
            raise CapError(f"step {i} ({op}): {e}") from None
        except TypeError as e:
            raise CapError(f"step {i} ({op}): bad arguments: {e}") from None
        if alias:
            created = res.get("created")
            if not created:
                raise CapError(f"step {i}: 'as' is only valid on create-* ops")
            aliases[alias] = created["uuid"]
        results.append({"op": op, **({"as": alias} if alias else {}), **res})
    return results

OPS.update(_chains.OPS)
