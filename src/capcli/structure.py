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
    "data": {k: "DataPkg" for k in ("oa", "sa", "la", "pa")},
    "interface": {k: "InterfacePkg" for k in ("oa", "sa", "la", "pa")},
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


def _check_target(elem, target) -> str:
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


def _move_part(model, comp, new_parent) -> dict[str, Any] | None:
    """Move the component's Part next to the component (see module doc)."""
    parts = [p for p in model.search("Part") if p.type == comp]
    if not parts:
        return None
    part = parts[0]
    if part.parent == new_parent:
        return None
    el = part._element
    el.getparent().remove(el)
    el.tag = "ownedParts" if type_name(new_parent).endswith("Pkg") else "ownedFeatures"
    new_parent._element.append(el)
    return brief(part)


def _rehome_links(model, moved) -> list[dict[str, Any]]:
    """Move exchanges/links touching ``moved`` (or below it) to their common owner."""
    key = require_layer(moved)
    inside = {moved.uuid} | {e.get("id") for e in moved._element.iter() if isinstance(e.tag, str) and e.get("id")}
    rehomed = []

    def ends_of(link):
        if type_name(link) == "PhysicalLink":
            return [e.parent for e in link.ends]
        return [endpoint_owner(link.source), endpoint_owner(link.target)]

    candidates = []
    if is_function(moved):
        candidates = [(x, "exchanges", is_function, root_function(model, key))
                      for x in model.search("FunctionalExchange", below=getattr(model, key))]
    elif is_component(moved):
        attr_ok = lambda o: hasattr(o, "component_exchanges")  # noqa: E731
        candidates = [(x, "component_exchanges", attr_ok, root_component(model, key))
                      for x in model.search("ComponentExchange", below=getattr(model, key))]
        if key == "pa":
            candidates += [(x, "physical_links", lambda o: hasattr(o, "physical_links"), root_component(model, key))
                           for x in model.search("PhysicalLink", below=model.pa)]
    for link, attr, pred, fallback in candidates:
        a, b = ends_of(link)
        if a is None or b is None or not ({a.uuid, b.uuid} & inside):
            continue
        owner = common_owner(a, b, pred, fallback)
        if key == "oa" and attr == "component_exchanges" and hasattr(owner, "communication_means"):
            attr = "communication_means"
        if link.parent != owner and hasattr(owner, attr):
            getattr(owner, attr).append(link)
            rehomed.append({**brief(link), "to": brief(owner)})
    return rehomed


def move(model, element: str, to: str):
    elem, target = resolve(model, element), resolve(model, to)
    if elem.parent == target:
        return {"unchanged": True, "reason": "already there"}
    attr = _check_target(elem, target)
    old_parent = elem.parent
    getattr(target, attr).append(elem)
    result: dict[str, Any] = {"moved": brief(elem), "from": brief(old_parent), "to": brief(target)}
    if is_component(elem) and layer_key(elem) != "oa":
        part = _move_part(model, elem, target)
        if part:
            result["part_moved"] = part
    rehomed = _rehome_links(model, elem) if (is_function(elem) or is_component(elem)) else []
    if rehomed:
        result["rehomed"] = rehomed
    return result


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


OPS = {
    "create-package": create_package,
    "move": move,
}
