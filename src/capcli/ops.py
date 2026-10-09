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

from capellambse import loader as _loader
from lxml import etree

from . import capabilities as _capabilities
from . import chains as _chains
from . import data as _data
from . import interfaces as _interfaces
from . import modes as _modes
from . import physical as _physical
from . import status as _status
from . import structure as _structure
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


def _check_structure_rules(key: str, parent, actor: bool, root=None) -> None:
    """Enforce the Arcadia structure rules that capellambse does not.

    - System Analysis treats the system as a black box: the System is its
      only component, so no sub-systems (or second systems) can be created.
    - Actors are external to the system: in SA, LA and PA they live in the
      Structure package (or a sub-package), never inside a component.
    - In LA and PA the logical/physical system is the only non-actor
      component directly in the Structure package: a second one breaks
      capellambse's ``root_component``. (Packages *inside* the system may
      hold components: Capella does that.)
    """
    if key == "oa":
        return
    in_component = _is_component(parent)
    if actor and in_component:
        raise CapError(
            f"Actors are outside the system: create them in the Structure package "
            f"(--parent {key}:structure or one of its sub-packages), not inside "
            f"{type_name(parent)} {parent.name!r}"
        )
    if key == "sa" and not actor:
        raise CapError(
            "System Analysis treats the system as a black box: the System is the "
            "only SA component. Decompose it in the Logical Architecture "
            "(--parent la:root-component), or create an external system as an "
            "actor (--actor --parent sa:structure)"
        )
    if not actor and root is not None and parent == root.parent:
        raise CapError(
            f"{root.name!r} is the only top-level component of {key.upper()}: create "
            f"components inside it (--parent {key}:root-component), or an actor "
            f"(--actor --parent {key}:structure)"
        )


def structure_violations(model) -> list[dict[str, Any]]:
    """Existing elements that break the rules of ``_check_structure_rules``."""
    out = []
    for key in ("sa", "la", "pa"):
        lay = getattr(model, key)
        root = root_component(model, key)
        for comp in lay.all_components:
            parent = comp.parent
            actor = bool(getattr(comp, "is_actor", False))
            problem = None
            if actor and _is_component(parent):
                problem = f"actor inside {parent.name!r}; actors belong in the Structure package"
            elif key == "sa" and not actor and comp != root:
                problem = (
                    "SA is a black box: the System must be the only SA component; "
                    "model sub-systems as logical components in LA"
                )
            elif not actor and comp != root and parent == root.parent:
                problem = (
                    f"second top-level component next to {root.name!r}; move it inside "
                    f"(`capcli move <uuid> {key}:root-component`)"
                )
            if problem:
                out.append({**brief(comp), "layer": key, "problem": problem})
    return out


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
        and _xtype(child).endswith("Realization")
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
            and _xtype(child).endswith("Realization")
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
        removed.append({"uuid": link.get("id"), "type": _xtype(link)})
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


# -------------------------------------------------------------------- delete

# Relationship elements that are meaningless once one of their ends is gone
# and can therefore be removed together with it (``delete --cascade``).
# Matching the metaclass alone is not enough: ``CapabilityRealization`` (an
# LA/PA capability) or ``StateTransition`` also match, and must not be
# deleted because a constraint they point to (``preCondition``, ``guard``)
# goes. So a referrer is cascaded only when it references through one of its
# *end* attributes (``_END_ATTRS``).
_CASCADABLE = re.compile(
    r"(Exchange|CommunicationMean|Allocation|Realization|Involvement\w*|"
    r"Link|^Part|Port|Trace|Generalization|Include|Extend|Exploitation|StateTransition|"
    r"InterfaceImplementation|InterfaceUse)$"
)
_END_ATTRS = {
    "source", "target", "sourceElement", "targetElement", "involved", "linkEnds",
    "abstractType", "super", "sub", "included", "extended", "capability",
    "allocatedItem", "implementedInterface", "usedInterface", "location", "deployedElement",
}
_EXCHANGE = re.compile(r"(Exchange|CommunicationMean)$")
# List-valued references that only say "this exchange/port carries that item".
# Deleting the item must detach it from them, never delete the carrier.
_DETACHABLE_ATTRS = {
    "exchangedItems", "convoyedInformations", "incomingExchangeItems", "outgoingExchangeItems",
    "availableInStates",
    # A transition's effects (functions) and triggers (exchanges, items):
    # deleting one of those must not delete the transition.
    "effect", "triggers",
    # A state's entry/exit/do activities (functions).
    "entry", "exit", "doActivity",
    # Interfaces provided/required by component ports.
    "providedInterfaces", "requiredInterfaces",
    # Constraints an element points to: deleting the constraint clears them.
    "preCondition", "postCondition", "guard", "exchangeContext",
}
# Same, for attribute names that are too generic to detach on every element.
_DETACHABLE_TYPED = {("PhysicalLinkCategory", "links")}


def _detachable(el, attr: str) -> bool:
    return attr in _DETACHABLE_ATTRS or (_xtype(el), attr) in _DETACHABLE_TYPED


# Caches Capella keeps on regions and states (see modes.py): always updated,
# never a reason to refuse a delete.
_ALWAYS_DETACH_ATTRS = {"involvedStates", "referencedStates"}
_REF_TOKEN = re.compile(r"#([A-Za-z0-9_-]+)$")
# Free-text attributes: a value like "#42" there is text, not a reference.
_NON_REF_ATTRS = {"id", "name", "description", "summary", "review", "sid", "value", "text",
                  "label", "comment", "documentation", "content"}
# Port attributes that make a port meaningful on its own (see ``delete``).
_PORT_CONTENT_ATTRS = ("providedInterfaces", "requiredInterfaces",
                       "incomingExchangeItems", "outgoingExchangeItems")
_TEXT_LINK = re.compile(r"(?:hlink://|href=\")#?([A-Za-z0-9_-]{8,})")


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
                if attr in _NON_REF_ATTRS or attr.startswith("ReqIF") or "#" not in value:
                    continue
                tokens = value.split()
                ids = [m.group(1) for t in tokens if (m := _REF_TOKEN.search(t))]
                if len(ids) != len(tokens):
                    continue  # free text that happens to contain '#'
                for i in ids:
                    yield el, attr, i


def iter_text_links(model):
    """Yield (element, id) for links inside text: descriptions and constraint bodies."""
    for root in _semantic_roots(model):
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            texts = [el.get("description") or ""]
            if el.tag == "bodies":
                texts.append(el.text or "")
            for text in texts:
                if "hlink://" in text or "href=" in text:
                    for m in _TEXT_LINK.finditer(text):
                        yield _owning_element(el), m.group(1)


def _owning_element(el):
    while el is not None and el.get("id") is None:
        el = el.getparent()
    return el


def delete(model, element: str, cascade: bool = False):
    obj = resolve(model, element)
    if layer_key(obj) is None or obj._element.getparent() is None:
        raise CapError("Refusing to delete a layer or model root")
    key = _require_layer(obj)
    layer_el = obj.layer._element
    if obj._element.getparent() is layer_el or obj in (
        root_function(model, key),
        root_component(model, key),
    ):
        raise CapError("Refusing to delete a layer's root package, function or component; "
                       "delete what is inside it instead")

    doomed = {obj._element}
    if _is_component(obj):
        # The component's own Parts go with it: they are its instances, not
        # independent referrers (otherwise even a fresh component needs --cascade).
        doomed.update(p._element for p in model.search("Part") if p.type == obj)

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
            if attr in _ALWAYS_DETACH_ATTRS:
                continue  # detached below
            if info["type"] == "PhysicalPathInvolvement":
                # A path missing a hop is meaningless: cascade to the whole path.
                if cascade:
                    extra.append(owner.getparent())
                else:
                    path = owner.getparent()
                    blockers.append({"uuid": path.get("id"), "type": _xtype(path), "name": path.get("name"), "via": "involvement"})
                continue
            if _detachable(el, attr):
                if not cascade:
                    blockers.append(info)
                continue  # detached below
            if cascade and el is owner and attr in _END_ATTRS and _CASCADABLE.search(info["type"]):
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
    detached = []
    for el, attr, ref in list(iter_refs(model)):
        if (_detachable(el, attr) or attr in _ALWAYS_DETACH_ATTRS) and ref in ids and not inside_doomed(el):
            if el.get(attr) is None:
                continue  # the same id was listed twice and is already gone
            tokens = [t for t in el.get(attr).split() if t.rpartition("#")[2] != ref]
            if tokens:
                el.set(attr, " ".join(tokens))
            else:
                del el.attrib[attr]
            detached.append({"from": el.get("id"), "type": _xtype(el), "attr": attr, "item": ref})
    affected_chains = _chains_losing_parts(model, doomed, inside_doomed)
    text_links = [{"uuid": o.get("id"), "type": _xtype(o), "name": o.get("name"), "links_to": i}
                  for o, i in iter_text_links(model) if i in ids and o is not None and not inside_doomed(o)]
    removed = []
    for el in doomed:
        if any(a in doomed for a in el.iterancestors()):
            continue
        removed.append({"uuid": el.get("id"), "type": _xtype(el), "name": el.get("name")})
        remove_xml(model, el)

    still_used = {ref for _, _, ref in iter_refs(model)}
    for pid in port_ids - ids - still_used:
        try:
            port = model._loader[pid]
        except KeyError:
            continue
        if any(port.get(a) for a in _PORT_CONTENT_ATTRS):
            continue  # it still provides interfaces or carries items: keep it
        if _xtype(port).endswith("Port") and port.getparent() is not None:
            removed.append({"uuid": pid, "type": _xtype(port), "name": port.get("name")})
            remove_xml(model, port)

    removed_ids = ids | {r["uuid"] for r in removed}
    diagram_refs = {ref for _, _, ref in iter_refs(model, visual=True) if ref in removed_ids}
    result: dict[str, Any] = {"deleted": removed}
    if detached:
        result["detached"] = detached
    if affected_chains:
        result["affected_chains"] = affected_chains
        result["chain_warning"] = ("functional chains lost functions or exchanges; "
                                   "run `capcli chain show <chain>` and fix their issues")
    if text_links:
        result["text_links"] = text_links
        result["text_warning"] = ("descriptions or constraint texts still link to deleted "
                                  "elements; edit them with `capcli set`")
    if diagram_refs:
        result["warning"] = (
            f"{len(diagram_refs)} deleted element(s) appear on diagrams; open the "
            "model in Capella and refresh/clean the affected diagrams"
        )
    return result


def _chains_losing_parts(model, doomed, inside_doomed) -> list[dict[str, Any]]:
    """Chains that keep existing but lose some of their involvements."""
    out, seen = [], set()
    for d in doomed:
        for e in d.iter():
            if not isinstance(e.tag, str) or not _xtype(e).startswith("FunctionalChainInvolvement"):
                continue
            chain = e.getparent()
            if chain is None or inside_doomed(chain) or chain.get("id") in seen:
                continue
            seen.add(chain.get("id"))
            out.append({"uuid": chain.get("id"), "type": _xtype(chain), "name": chain.get("name")})
    return out


def _dedupe(items):
    seen, out = set(), []
    for i in items:
        if i["uuid"] not in seen:
            seen.add(i["uuid"])
            out.append(i)
    return out


# --------------------------------------------------------------------- check

_REQUIRED_REF_ATTRS = ("source", "target", "sourceElement", "targetElement", "abstractType")


def check(model, fix: bool = False) -> dict[str, Any]:
    """Look for dangling or empty references in the semantic model.

    Also reports realization links without ``sourceElement`` (written by
    earlier capcli versions or by plain capellambse); ``fix`` fills them in.
    """
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
    incomplete, fixed = [], 0
    for root in _semantic_roots(model):
        for el in root.iter():
            if (
                isinstance(el.tag, str)
                and _xtype(el).endswith("Realization")
                and el.get("targetElement")
                and el.get("sourceElement") is None
            ):
                owner = _owning_element(el.getparent())
                if fix and owner is not None:
                    el.set("sourceElement", "#" + owner.get("id"))
                    fixed += 1
                else:
                    incomplete.append({"uuid": el.get("id"), "type": _xtype(el), "missing": "sourceElement"})
    structure = structure_violations(model)
    caches = _modes.cache_mismatches(model, fix=fix)
    bad_impl = _interfaces.bad_implementations(model, fix=fix)
    if fix:
        fixed += len(caches) + len(bad_impl)
        caches, bad_impl = [], []
    res: dict[str, Any] = {
        "ok": not dangling and not empty and not incomplete and not structure and not caches
        and not bad_impl,
        "dangling": dangling,
        "empty": empty,
        "incomplete": incomplete,
        "structure": structure,
        "state_caches": caches,
        "interface_implementations": bad_impl,
    }
    if fix:
        res["fixed"] = fixed
    return res


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
