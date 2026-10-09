"""Interfaces: creation, exchange items, provide/require, allocation.

Two capellambse 0.8.1 bugs are worked around here, at XML level:

- ``Component.implemented_interfaces`` writes the attribute
  ``implementedInterfaces`` (plural) on the ``InterfaceImplementation``;
  Capella's metamodel and every Capella-written model use
  ``implementedInterface``. So implementations are written (and read) here,
  and ``check`` reports/fixes the plural form;
- ``InterfaceAllocation`` is declared abstract, so ``allocated_interfaces``
  cannot create one. capellambse's own definition gives the shape (owned in
  ``ownedInterfaceAllocations``, ``targetElement`` = interface,
  ``sourceElement`` = allocator), the same as every other Allocation.

Interfaces, like data, are visible downwards: an element may use an interface
of its own layer or of a layer above.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .model import (
    LAYERS,
    CapError,
    add_xml_child,
    brief,
    cs_alias,
    is_component,
    label,
    layer,
    layer_key,
    remove_xml,
    require_layer,
    resolve,
    toggle,
    type_name,
    with_status,
)

BAD_IMPL_ATTR = "implementedInterfaces"  # what capellambse writes
IMPL_ATTR = "implementedInterface"  # what Capella writes


def _interface(model, ref):
    obj = resolve(model, ref)
    if type_name(obj) != "Interface":
        raise CapError(f"Expected an interface, got {type_name(obj)} {obj.uuid}")
    return obj


def _visible(user, iface) -> None:
    ku, ki = require_layer(user), require_layer(iface)
    if LAYERS.index(ki) > LAYERS.index(ku):
        raise CapError(f"{label(iface)} is in {ki} and is not visible from {ku}: "
                       "interfaces come from the same layer or a layer above")


def _children(obj, tag: str, attr: str, target_uuid: str | None = None):
    out = []
    for c in obj._element:
        if c.tag == tag:
            ref = (c.get(attr) or c.get(BAD_IMPL_ATTR if attr == IMPL_ATTR else attr) or "")
            if target_uuid is None or ref.rpartition("#")[2] == target_uuid:
                out.append(c)
    return out


# ---------------------------------------------------------------- operations


def create_interface(model, name: str, layer_name: str | None = None, parent: str | None = None,
                     description: str | None = None):
    if parent:
        pkg = resolve(model, parent)
        if type_name(pkg) != "InterfacePkg":
            raise CapError(f"--parent must be an interface package, got {type_name(pkg)}; or use --layer")
    elif layer_name:
        pkg = layer(model, layer_name).interface_pkg
    else:
        raise CapError("Give --layer or --parent (an interface package)")
    iface = pkg.interfaces.create("Interface", name=name)
    if description:
        iface.description = description
    return {"created": brief(iface), "parent": brief(pkg)}


def set_items(model, interface: str, elements: list[str], remove: bool = False):
    """Exchange items an interface carries (ExchangeItemAllocation)."""
    iface = _interface(model, interface)
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for ref in elements:
        ei = resolve(model, ref)
        if type_name(ei) != "ExchangeItem":
            raise CapError(f"Interfaces carry exchange items, not {type_name(ei)}")
        if LAYERS.index(require_layer(ei)) > LAYERS.index(require_layer(iface)):
            raise CapError(f"{label(ei)} is in a layer below the interface and is not visible from it")
        done = toggle(iface.allocated_exchange_items, ei, remove, f"{label(iface)} does not carry {label(ei)}")
        (changed if done else unchanged).append(brief(ei))
    return {"interface": brief(iface), "removed" if remove else "added": changed, "unchanged": unchanged}


def _port_or_component(model, ref):
    obj = resolve(model, ref)
    if type_name(obj) == "ComponentPort" or is_component(obj):
        return obj
    raise CapError(f"Interfaces are provided/required by components or component ports, not {type_name(obj)}")


def provide(model, element: str, interface: str, remove: bool = False):
    """A component implements, or a component port provides, an interface."""
    obj, iface = _port_or_component(model, element), _interface(model, interface)
    _visible(obj, iface)
    if type_name(obj) == "ComponentPort":
        return _port_link(obj, iface, "provided_interfaces", remove)
    links = _children(obj, "ownedInterfaceImplementations", IMPL_ATTR, iface.uuid)
    if remove:
        if not links:
            raise CapError(f"{label(obj)} does not implement {label(iface)}")
        for el in links:
            remove_xml(model, el)
        return {"element": brief(obj), "no_longer_provides": brief(iface)}
    if links:
        return {"unchanged": True, "reason": "already provided"}
    add_xml_child(model, obj._element, "ownedInterfaceImplementations",
                  f"{cs_alias(model, obj._element)}:InterfaceImplementation",
                  **{IMPL_ATTR: "#" + iface.uuid})
    return {"element": brief(obj), "provides": brief(iface)}


def require(model, element: str, interface: str, remove: bool = False):
    """A component uses, or a component port requires, an interface."""
    obj, iface = _port_or_component(model, element), _interface(model, interface)
    _visible(obj, iface)
    if type_name(obj) == "ComponentPort":
        return _port_link(obj, iface, "required_interfaces", remove)
    present = iface in obj.used_interfaces
    if remove:
        if not present:
            raise CapError(f"{label(obj)} does not use {label(iface)}")
        obj.used_interfaces.remove(iface)
        return {"element": brief(obj), "no_longer_requires": brief(iface)}
    if present:
        return {"unchanged": True, "reason": "already required"}
    obj.used_interfaces.append(iface)
    return {"element": brief(obj), "requires": brief(iface)}


def _port_link(port, iface, attr: str, remove: bool):
    lst = getattr(port, attr)
    verb = "provides" if attr == "provided_interfaces" else "requires"
    if remove:
        if iface not in lst:
            raise CapError(f"{label(port)} does not {verb[:-1]} {label(iface)}")
        lst.remove(iface)
        return {"element": brief(port), f"no_longer_{verb}": brief(iface)}
    if iface in lst:
        return {"unchanged": True, "reason": f"already {verb[:-1]}d"}
    lst.append(iface)
    return {"element": brief(port), verb: brief(iface)}


def allocate_interface(model, element: str, interface: str, remove: bool = False):
    """InterfaceAllocation from a component or interface to an interface."""
    obj, iface = resolve(model, element), _interface(model, interface)
    if not (is_component(obj) or type_name(obj) == "Interface"):
        raise CapError(f"Interfaces are allocated to components or interfaces, not {type_name(obj)}")
    if obj == iface:
        raise CapError("An interface cannot be allocated to itself")
    _visible(obj, iface)
    links = _children(obj, "ownedInterfaceAllocations", "targetElement", iface.uuid)
    if not remove and not links and type_name(obj) == "Interface" and obj in _allocated_closure(model, iface):
        raise CapError(f"{label(iface)} already allocates {label(obj)} (directly or not): "
                       "this allocation would create a cycle")
    if remove:
        if not links:
            raise CapError(f"{label(iface)} is not allocated to {label(obj)}")
        for el in links:
            remove_xml(model, el)
        return {"element": brief(obj), "no_longer_allocates": brief(iface)}
    if links:
        return {"unchanged": True, "reason": "already allocated"}
    add_xml_child(model, obj._element, "ownedInterfaceAllocations",
                  f"{cs_alias(model, obj._element)}:InterfaceAllocation",
                  targetElement="#" + iface.uuid, sourceElement="#" + obj.uuid)
    return {"element": brief(obj), "allocates": brief(iface)}


def _allocated_closure(model, iface) -> list:
    """Interfaces that ``iface`` allocates, directly or through other interfaces."""
    seen, todo, out = {iface.uuid}, [iface], []
    while todo:
        cur = todo.pop()
        for el in _children(cur, "ownedInterfaceAllocations", "targetElement"):
            ref = (el.get("targetElement") or "").rpartition("#")[2]
            if ref and ref not in seen:
                seen.add(ref)
                try:
                    nxt = model.by_uuid(ref)
                except KeyError:
                    continue
                out.append(nxt)
                todo.append(nxt)
    return out


# --------------------------------------------------------------------- reads


def _referrers(model, xpath: str):
    """Elements matched by xpath; link elements report their owner instead."""
    out = []
    for el in model._loader.xpath(xpath):
        owner = el.getparent() if el.tag in LINK_TAGS else el
        try:
            out.append(brief(model.by_uuid(owner.get("id"))))
        except KeyError:
            continue
    return out


LINK_TAGS = ("ownedInterfaceImplementations", "ownedInterfaceUses", "ownedInterfaceAllocations")


def show(model, uuid: str) -> dict[str, Any]:
    iface = _interface(model, uuid)
    i = iface.uuid
    d = with_status({**brief(iface), "layer": layer_key(iface), "parent": brief(iface.parent)}, iface)
    if iface.description:
        d["description"] = str(iface.description)
    d["exchange_items"] = [brief(ei) for ei in iface.allocated_exchange_items]
    d["provided_by"] = _referrers(model, f"//ownedInterfaceImplementations[@{IMPL_ATTR}='#{i}' or @{BAD_IMPL_ATTR}='#{i}']")
    d["required_by"] = _referrers(model, f"//ownedInterfaceUses[@usedInterface='#{i}']")
    d["provided_by_ports"] = _referrers(model, f"//*[contains(concat(' ', @providedInterfaces, ' '), ' #{i} ')]")
    d["required_by_ports"] = _referrers(model, f"//*[contains(concat(' ', @requiredInterfaces, ' '), ' #{i} ')]")
    d["allocated_to"] = _referrers(model, f"//ownedInterfaceAllocations[@targetElement='#{i}']")
    issues = []
    if not d["exchange_items"]:
        issues.append("interface carries no exchange items")
    if (d["required_by"] or d["required_by_ports"]) and not (d["provided_by"] or d["provided_by_ports"]):
        issues.append("interface is required but nothing provides it")
    d["issues"] = issues
    return d


def list_interfaces(model, layer_name: str):
    items = [{**brief(i), "items": len(i.allocated_exchange_items)}
             for i in model.search("Interface", below=layer(model, layer_name))]
    return {"layer": layer_name, "count": len(items), "items": items}


def bad_implementations(model, fix: bool = False) -> list[dict[str, Any]]:
    """InterfaceImplementations written by capellambse with the wrong attribute."""
    out = []
    for el in model._loader.xpath(f"//ownedInterfaceImplementations[@{BAD_IMPL_ATTR}]"):
        out.append({"uuid": el.get("id"), "type": "InterfaceImplementation",
                    "problem": f"attribute {BAD_IMPL_ATTR!r} instead of {IMPL_ATTR!r}"})
        if fix:
            el.set(IMPL_ATTR, el.get(BAD_IMPL_ATTR))
            del el.attrib[BAD_IMPL_ATTR]
    return out


OPS: dict[str, Callable[..., Any]] = {
    "create-interface": create_interface,
    "interface-items": set_items,
    "provide-interface": provide,
    "require-interface": require,
    "allocate-interface": allocate_interface,
}
