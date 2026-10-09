"""Structure tools: packages, moving elements, and repairing structure.

capellambse moves an element when it is appended to another containment
list, but nothing else follows it. Two things must:

- a component's **Part**: Capella keeps it in the component's parent
  (``ownedFeatures`` of a component, ``ownedParts`` of a package). It stays
  behind in the old parent otherwise;
- the **exchanges and physical links** attached to the moved element (or
  anything below it): Capella owns them in the nearest common parent of
  their two ends, which may change with the move.

Both are handled here, and the Arcadia structure rules of ``ops.py``
(no sub-systems in SA, actors only in packages) are applied to moves too.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .model import (
    CapError,
    brief,
    common_owner,
    endpoint_owner,
    is_component,
    is_function,
    layer_key,
    require_layer,
    resolve,
    root_component,
    root_function,
    structure_pkg,
    type_name,
)

# Package metaclass per kind and layer.
PKG_TYPES = {
    "function": {"oa": "OperationalActivityPkg", "sa": "SystemFunctionPkg",
                 "la": "LogicalFunctionPkg", "pa": "PhysicalFunctionPkg"},
    "component": {"oa": "EntityPkg", "sa": "SystemComponentPkg",
                  "la": "LogicalComponentPkg", "pa": "PhysicalComponentPkg"},
    "capability": {"oa": "OperationalCapabilityPkg", "sa": "CapabilityPkg",
                   "la": "CapabilityRealizationPkg", "pa": "CapabilityRealizationPkg"},
    "data": dict.fromkeys(("oa", "sa", "la", "pa"), "DataPkg"),
    "interface": dict.fromkeys(("oa", "sa", "la", "pa"), "InterfacePkg"),
}
DATA_TYPES = ("Class", "Union", "Collection", "Enumeration", "BooleanType", "NumericType",
              "StringType", "PhysicalQuantity", "ExchangeItem")
CAPABILITY_TYPES = ("OperationalCapability", "Capability", "CapabilityRealization")


def _pkg_kind(obj) -> str | None:
    """Which package kind obj is, or can hold sub-packages of."""
    t = type_name(obj)
    for kind, types in PKG_TYPES.items():
        if t in types.values():
            return kind
    if is_function(obj):
        return "function"
    if is_component(obj):
        return "component"
    return None


def _pkg_list(obj):
    """The containment list where obj keeps its sub-packages."""
    if type_name(obj) == "PhysicalComponent":
        return obj.component_pkgs
    return obj.packages


# ------------------------------------------------------------------ packages


def create_package(model, parent: str, name: str):
    par = resolve(model, parent)
    key = require_layer(par)
    kind = _pkg_kind(par)
    if kind is None:
        raise CapError(
            f"{type_name(par)} cannot hold packages; use a package, function or component "
            "(e.g. la:functions, la:structure, la:capabilities, la:data, la:interfaces)"
        )
    if kind == "component" and key == "sa" and is_component(par):
        raise CapError("System Analysis is a black box: put packages in sa:structure, not inside the System")
    pkg = _pkg_list(par).create(PKG_TYPES[kind][key], name=name)
    return {"created": brief(pkg), "parent": brief(par), "kind": kind}


# ---------------------------------------------------------------------- move


def _check_target(model, elem, target) -> str:
    """Validate a move and return the attribute of target that will hold elem."""
    ke, kt = require_layer(elem), require_layer(target)
    if ke != kt:
        raise CapError(f"Elements move within their layer ({ke}), not to {kt}")
    if target == elem or any(a == elem for a in _all_ancestors(target)):
        raise CapError("Cannot move an element into itself or one of its descendants")
    te, tt = type_name(elem), type_name(target)
    tkind = _pkg_kind(target)
    if is_function(elem):
        if tkind != "function":
            raise CapError(f"Functions move into a function or a function package, not {tt}")
        return "functions"
    if is_component(elem):
        actor = bool(getattr(elem, "is_actor", False))
        if tkind != "component":
            raise CapError(f"Components move into a component or a component package, not {tt}")
        if ke != "oa" and actor and is_component(target):
            raise CapError(f"Actors live in the Structure package (or a sub-package), not inside {tt}")
        if ke == "sa" and is_component(target):
            raise CapError("System Analysis is a black box: nothing moves inside the System")
        if ke in ("la", "pa") and not actor and target == structure_pkg(model, ke):
            raise CapError(f"Only actors and the {ke.upper()} root component live directly in the "
                           f"Structure package; move components inside {ke}:root-component")
        if ke == "oa":
            return "entities"
        return "owned_components" if tt == "PhysicalComponent" else "components"
    if tkind is not None and type_name(elem) in [t for types in PKG_TYPES.values() for t in types.values()]:
        if _pkg_kind(elem) != tkind:
            raise CapError(f"A {_pkg_kind(elem)} package moves into a {_pkg_kind(elem)} package (or function/component), not {tt}")
        return "component_pkgs" if tt == "PhysicalComponent" else "packages"
    if te in CAPABILITY_TYPES:
        if tkind != "capability" or tt not in PKG_TYPES["capability"].values():
            raise CapError(f"Capabilities move into a capability package, not {tt}")
        return "capabilities"
    if te in DATA_TYPES:
        if tt != "DataPkg":
            raise CapError(f"Data elements move into a data package, not {tt}")
        return {"Class": "classes", "ExchangeItem": "exchange_items", "Collection": "collections"}.get(te, "data_types")
    if te in ("FunctionalChain", "OperationalProcess"):
        if not is_function(target):
            raise CapError(f"Functional chains are owned by functions, not {tt}")
        return "functional_chains"
    raise CapError(
        f"Moving {te} is not supported; supported: functions, components, actors, packages, "
        "capabilities, data elements, functional chains"
    )


def _all_ancestors(obj):
    p = getattr(obj, "parent", None)
    while p is not None:
        yield p
        p = getattr(p, "parent", None)


def _move_part(model, comp, old_parent, new_parent) -> dict[str, Any] | None:
    """Move the component's Part next to the component (see module doc).

    Only the Part held by the old parent moves: in multi-part models a
    component can have several Parts elsewhere, which stay where they are.
    """
    parts = [p for p in model.search("Part") if p.type == comp and p.parent == old_parent]
    if not parts:
        return None
    part = parts[0]
    el = part._element
    el.getparent().remove(el)
    el.tag = "ownedParts" if type_name(new_parent).endswith("Pkg") else "ownedFeatures"
    new_parent._element.append(el)
    return brief(part)


def _rehome_links(model, moved) -> list[dict[str, Any]]:
    """Move exchanges/links touching ``moved`` (or below it) to their common owner.

    ``moved`` may be a function, a component or a package: whatever moved
    with it counts, so a package move re-homes the exchanges of its content.
    """
    key = require_layer(moved)
    inside = {moved.uuid} | {e.get("id") for e in moved._element.iter() if isinstance(e.tag, str) and e.get("id")}
    rehomed = []
    lay = getattr(model, key)

    def ends_of(link):
        if type_name(link) == "PhysicalLink":
            return [e.parent for e in link.ends]
        return [endpoint_owner(link.source), endpoint_owner(link.target)]

    def has(attr):
        return lambda o: hasattr(o, attr)

    candidates = [(x, "exchanges", is_function, root_function(model, key))
                  for x in model.search("FunctionalExchange", below=lay)]
    if key == "oa":
        # OA entity links are CommunicationMeans, owned by entities or the entity package.
        candidates += [(x, "communication_means", has("communication_means"), root_component(model, key))
                       for x in model.search("CommunicationMean", below=lay)]
    else:
        candidates += [(x, "component_exchanges", has("component_exchanges"), root_component(model, key))
                       for x in model.search("ComponentExchange", below=lay)]
    if key == "pa":
        candidates += [(x, "physical_links", has("physical_links"), root_component(model, key))
                       for x in model.search("PhysicalLink", below=lay)]
    for link, attr, pred, fallback in candidates:
        a, b = ends_of(link)
        if a is None or b is None or not ({a.uuid, b.uuid} & inside):
            continue
        owner = common_owner(a, b, pred, fallback)
        if link.parent != owner and hasattr(owner, attr):
            getattr(owner, attr).append(link)
            rehomed.append({**brief(link), "to": brief(owner)})
    return rehomed


def _refuse_roots(model, elem, verb: str) -> None:
    key = require_layer(elem)
    parent = elem._element.getparent()
    if (parent is None or parent is elem.layer._element
            or elem in (root_function(model, key), root_component(model, key))):
        raise CapError(f"Cannot {verb} a layer's root package, function or component; "
                       f"{verb} what is inside it instead")


def move(model, element: str, to: str):
    elem, target = resolve(model, element), resolve(model, to)
    _refuse_roots(model, elem, "move")
    if elem.parent == target:
        return {"unchanged": True, "reason": "already there"}
    attr = _check_target(model, elem, target)
    old_parent = elem.parent
    getattr(target, attr).append(elem)
    result: dict[str, Any] = {"moved": brief(elem), "from": brief(old_parent), "to": brief(target)}
    if is_component(elem):
        part = _move_part(model, elem, old_parent, target)
        if part:
            result["part_moved"] = part
    rehomed = _rehome_links(model, elem)
    if rehomed:
        result["rehomed"] = rehomed
    return result


def reorder(model, element: str, before: str | None = None, after: str | None = None,
            first: bool = False, last: bool = False):
    """Move an element among its siblings (the order Capella's explorer shows)."""
    elem = resolve(model, element)
    if sum(bool(x) for x in (before, after, first, last)) != 1:
        raise CapError("Give exactly one of --before, --after, --first, --last")
    if layer_key(elem) is None or elem._element.getparent() is None:
        raise CapError("Cannot reorder a layer or the model root; reorder elements inside a layer")
    el = elem._element
    parent = el.getparent()
    siblings = [c for c in parent if c.tag == el.tag]
    if first or last:
        anchor_el = siblings[0] if first else siblings[-1]
        place_before = first
    else:
        anchor = resolve(model, before or after or "")
        anchor_el = anchor._element
        if anchor_el.getparent() is not parent or anchor_el.tag != el.tag:
            raise CapError("--before/--after must name a sibling in the same list (same parent, same kind)")
        place_before = bool(before)
    if anchor_el is el:
        return {"unchanged": True, "reason": "already there"}
    parent.remove(el)
    idx = list(parent).index(anchor_el)
    parent.insert(idx if place_before else idx + 1, el)
    order = [c.get("name") for c in parent if c.tag == el.tag]
    return {"moved": brief(elem), "order": order}


# -------------------------------------------------------------------- repair


def repair(model, apply: bool = True) -> dict[str, Any]:
    """Fix structure violations that have one safe answer; report the rest."""
    from_violations = []
    for key in ("sa", "la", "pa"):
        for comp in getattr(model, key).all_components:
            if getattr(comp, "is_actor", False) and is_component(comp.parent):
                from_violations.append((key, comp))
    moved = []
    for key, comp in from_violations:
        pkg = structure_pkg(model, key)
        if apply:
            moved.append(move(model, comp.uuid, pkg.uuid))
        else:
            moved.append({"would_move": brief(comp), "from": brief(comp.parent), "to": brief(pkg)})
    manual = []
    root = root_component(model, "sa")
    for comp in model.sa.all_components:
        if not getattr(comp, "is_actor", False) and comp != root:
            manual.append({
                **brief(comp),
                "problem": "sub-system or second system in System Analysis (a black box)",
                "options": [
                    "if it is external to the system: delete it and recreate it as an actor "
                    "(`create component --parent sa:structure --actor`)",
                    "if it is part of the system: delete it (`delete --cascade`) and model it as a "
                    "logical component in LA (`create component --parent la:root-component`), "
                    "reallocating its functions there",
                ],
            })
    return {"fixed" if apply else "would_fix": moved, "needs_decision": manual}


OPS: dict[str, Callable[..., Any]] = {
    "create-package": create_package,
    "move": move,
    "reorder": reorder,
}
