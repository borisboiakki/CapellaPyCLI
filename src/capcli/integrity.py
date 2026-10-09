"""Model integrity: references, delete, check, and the Arcadia structure rules.

Everything here works on the XML tree, because capellambse's own removal
leaves dangling references (see CLAUDE.md, "Low-level XML work"). ``ops``
imports from this module; it imports only ``model`` and the feature
modules whose caches ``check`` repairs.
"""

from __future__ import annotations

import re
from typing import Any

from capellambse import loader as _loader
from lxml import etree

from . import interfaces as _interfaces
from . import modes as _modes
from .model import (
    CapError,
    brief,
    layer_key,
    remove_xml,
    resolve,
    root_component,
    root_function,
    type_name,
)
from .model import is_component as _is_component
from .model import require_layer as _require_layer

# ------------------------------------------------------- Arcadia structure rules

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
    return attr in _DETACHABLE_ATTRS or (xml_type(el), attr) in _DETACHABLE_TYPED


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


def xml_type(elem) -> str:
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


class _Doomed:
    """The XML elements a delete removes (each with its whole subtree)."""

    def __init__(self, elements):
        self.elements = set(elements)

    def ids(self) -> set[str]:
        return {e.get("id") for d in self.elements for e in d.iter() if isinstance(e.tag, str) and e.get("id")}

    def contains(self, el) -> bool:
        return any(a in self.elements for a in [el, *el.iterancestors()])

    def tops(self):
        """Doomed elements not already inside another doomed one."""
        return [el for el in self.elements if not any(a in self.elements for a in el.iterancestors())]


def delete(model, element: str, cascade: bool = False):
    obj = resolve(model, element)
    _refuse_roots(model, obj)
    doomed = _Doomed([obj._element])
    if _is_component(obj):
        # The component's own Parts go with it: they are its instances, not
        # independent referrers (otherwise even a fresh component needs --cascade).
        doomed.elements.update(p._element for p in model.search("Part") if p.type == obj)
    _collect(model, doomed, cascade)

    ids = doomed.ids()
    port_ids = _exchange_ports(doomed)
    detached = _detach(model, doomed, ids)
    affected_chains = _chains_losing_parts(doomed)
    text_links = [{"uuid": o.get("id"), "type": xml_type(o), "name": o.get("name"), "links_to": i}
                  for o, i in iter_text_links(model) if i in ids and o is not None and not doomed.contains(o)]
    removed = []
    for el in doomed.tops():
        removed.append({"uuid": el.get("id"), "type": xml_type(el), "name": el.get("name")})
        remove_xml(model, el)
    removed += _remove_orphan_ports(model, port_ids - ids)

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


def _refuse_roots(model, obj) -> None:
    if layer_key(obj) is None or obj._element.getparent() is None:
        raise CapError("Refusing to delete a layer or model root")
    key = _require_layer(obj)
    if obj._element.getparent() is obj.layer._element or obj in (
        root_function(model, key),
        root_component(model, key),
    ):
        raise CapError("Refusing to delete a layer's root package, function or component; "
                       "delete what is inside it instead")


def _collect(model, doomed: _Doomed, cascade: bool) -> None:
    """Grow ``doomed`` with what cascades, or raise on what blocks the delete.

    Repeated until nothing new is found: cascading an exchange may orphan
    its allocation, and so on.
    """
    while True:
        ids = doomed.ids()
        blockers, extra = [], []
        for el, attr, ref in iter_refs(model):
            if ref not in ids or doomed.contains(el):
                continue
            verdict, target = _referrer_verdict(el, attr, cascade)
            if verdict == "cascade":
                extra.append(target)
            elif verdict == "block":
                blockers.append(target)
        if blockers:
            raise CapError(
                "Element is still referenced; "
                + ("these references cannot be cascaded: " if cascade else "re-run with --cascade or remove them first: ")
                + "; ".join(f"{b['type']} {b['name']!r} {b['uuid']} (via {b['via']})" for b in _dedupe(blockers))
            )
        new = [e for e in extra if e not in doomed.elements]
        if not new:
            return
        doomed.elements.update(new)


def _referrer_verdict(el, attr: str, cascade: bool):
    """What to do with ``el`` referencing a doomed element through ``attr``.

    Returns ("detach", None) for references cleared afterwards, ("cascade",
    element) for links deleted with it, or ("block", info) for referrers
    that stop the delete.
    """
    if attr in _ALWAYS_DETACH_ATTRS:
        return "detach", None
    owner = _owning_element(el)
    info = {"uuid": owner.get("id"), "type": xml_type(owner), "name": owner.get("name"), "via": attr}
    if info["type"] == "PhysicalPathInvolvement":
        # A path missing a hop is meaningless: cascade to the whole path.
        path = owner.getparent()
        if cascade:
            return "cascade", path
        return "block", {"uuid": path.get("id"), "type": xml_type(path), "name": path.get("name"), "via": "involvement"}
    if _detachable(el, attr):
        return ("detach", None) if cascade else ("block", info)
    if cascade and el is owner and attr in _END_ATTRS and _CASCADABLE.search(info["type"]):
        return "cascade", owner
    return "block", info


def _exchange_ports(doomed: _Doomed) -> set[str]:
    """Ports at the ends of the doomed exchanges (removed if left unused)."""
    return {
        e.get(a)[1:]
        for d in doomed.elements
        for e in d.iter()
        if isinstance(e.tag, str) and _EXCHANGE.search(xml_type(e))
        for a in ("source", "target")
        if (e.get(a) or "").startswith("#")
    }


def _detach(model, doomed: _Doomed, ids: set[str]) -> list[dict[str, Any]]:
    """Remove doomed ids from the reference lists that only mention them."""
    detached = []
    for el, attr, ref in list(iter_refs(model)):
        if (_detachable(el, attr) or attr in _ALWAYS_DETACH_ATTRS) and ref in ids and not doomed.contains(el):
            if el.get(attr) is None:
                continue  # the same id was listed twice and is already gone
            tokens = [t for t in el.get(attr).split() if t.rpartition("#")[2] != ref]
            if tokens:
                el.set(attr, " ".join(tokens))
            else:
                del el.attrib[attr]
            detached.append({"from": el.get("id"), "type": xml_type(el), "attr": attr, "item": ref})
    return detached


def _remove_orphan_ports(model, port_ids: set[str]) -> list[dict[str, Any]]:
    """Remove ports nothing references any more, unless they still mean something."""
    still_used = {ref for _, _, ref in iter_refs(model)}
    removed = []
    for pid in port_ids - still_used:
        try:
            port = model._loader[pid]
        except KeyError:
            continue
        if any(port.get(a) for a in _PORT_CONTENT_ATTRS):
            continue  # it still provides interfaces or carries items: keep it
        if xml_type(port).endswith("Port") and port.getparent() is not None:
            removed.append({"uuid": pid, "type": xml_type(port), "name": port.get("name")})
            remove_xml(model, port)
    return removed


def _chains_losing_parts(doomed: _Doomed) -> list[dict[str, Any]]:
    """Chains that keep existing but lose some of their involvements."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for d in doomed.elements:
        for e in d.iter():
            if not isinstance(e.tag, str) or not xml_type(e).startswith("FunctionalChainInvolvement"):
                continue
            chain = e.getparent()
            if chain is None or doomed.contains(chain) or chain.get("id") in seen:
                continue
            seen.add(chain.get("id"))
            out.append({"uuid": chain.get("id"), "type": xml_type(chain), "name": chain.get("name")})
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
            dangling.append({"uuid": owner.get("id"), "type": xml_type(owner), "attr": attr, "missing": ref})
    empty = []
    for root in _semantic_roots(model):
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for attr in _REQUIRED_REF_ATTRS:
                if el.get(attr) == "":
                    empty.append({"uuid": el.get("id"), "type": xml_type(el), "attr": attr})
    incomplete, fixed = [], 0
    for root in _semantic_roots(model):
        for el in root.iter():
            if (
                isinstance(el.tag, str)
                and xml_type(el).endswith("Realization")
                and el.get("targetElement")
                and el.get("sourceElement") is None
            ):
                owner = _owning_element(el.getparent())
                if fix and owner is not None:
                    el.set("sourceElement", "#" + owner.get("id"))
                    fixed += 1
                else:
                    incomplete.append({"uuid": el.get("id"), "type": xml_type(el), "missing": "sourceElement"})
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
