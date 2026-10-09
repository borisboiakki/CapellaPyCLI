"""Capabilities, their involvements and relations, and missions (SA).

Capella uses a different metaclass per layer:

- OA: ``OperationalCapability`` (involves entities, activities, processes)
- SA: ``Capability`` (involves the system and actors, functions, chains;
  exploited by missions)
- LA/PA: ``CapabilityRealization`` (involves components and actors,
  functions, chains)

Each involvement or relation is a small link element owned by the
capability. They are created and removed here directly so that they always
match what Capella writes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .model import (
    CHAIN_TYPES,
    COMPONENT_TYPE,
    CapError,
    brief,
    constraint_text,
    is_component,
    is_function,
    label,
    layer,
    remove_link,
    require_layer,
    resolve,
    same_layer,
    set_constraint,
    type_name,
    with_status,
)

CAPABILITY_TYPE = {
    "oa": "OperationalCapability",
    "sa": "Capability",
    "la": "CapabilityRealization",
    "pa": "CapabilityRealization",
}

# (containment attribute on the capability, link metaclass) per kind of
# involved element. Actors are components with is_actor=True in SA/LA/PA and
# entities in OA, so they share the component slot.
_COMPONENT_SLOT = {
    "oa": ("entity_involvements", "EntityOperationalCapabilityInvolvement"),
    "sa": ("involvements", "CapabilityInvolvement"),
    "la": ("capability_realization_involvements", "CapabilityRealizationInvolvement"),
    "pa": ("capability_realization_involvements", "CapabilityRealizationInvolvement"),
}
_FUNCTION_SLOT = ("function_involvements", "AbstractFunctionAbstractCapabilityInvolvement")
_CHAIN_SLOT = ("chain_involvements", "FunctionalChainAbstractCapabilityInvolvement")

# relation name -> (containment attribute, link metaclass, link attribute
# pointing at the other capability)
RELATIONS = {
    "include": ("includes", "AbstractCapabilityInclude", "included"),
    "extend": ("extends", "AbstractCapabilityExtend", "extended"),
    "generalize": ("generalizations", "AbstractCapabilityGeneralization", "super"),
}


def is_capability(obj) -> bool:
    return type_name(obj) in CAPABILITY_TYPE.values()


def _capability(model, ref: str):
    obj = resolve(model, ref)
    if not is_capability(obj):
        raise CapError(f"Expected a capability, got {type_name(obj)} {obj.uuid}")
    return obj


def _slot(cap, obj):
    """Return (attribute, link metaclass, label) for involving obj in cap."""
    key = require_layer(cap)
    if is_function(obj):
        return (*_FUNCTION_SLOT, "function")
    if type_name(obj) in CHAIN_TYPES:
        return (*_CHAIN_SLOT, "chain")
    if is_component(obj) and type_name(obj) == COMPONENT_TYPE[key]:
        what = "entity" if key == "oa" else ("actor" if getattr(obj, "is_actor", False) else "component")
        return (*_COMPONENT_SLOT[key], what)
    raise CapError(
        f"A {key} capability can involve functions, chains and "
        f"{'entities' if key == 'oa' else 'components/actors'}, not {type_name(obj)}"
    )


def _links(cap, attr):
    return list(getattr(cap, attr))


def involved(cap) -> dict[str, list]:
    key = require_layer(cap)
    comp_attr = _COMPONENT_SLOT[key][0]
    comps = [i.involved for i in _links(cap, comp_attr) if i.involved is not None]
    out = {
        "entities" if key == "oa" else "components": [
            brief(c) for c in comps if not getattr(c, "is_actor", False)
        ],
        "functions": [brief(i.involved) for i in _links(cap, _FUNCTION_SLOT[0]) if i.involved is not None],
        "chains": [brief(i.involved) for i in _links(cap, _CHAIN_SLOT[0]) if i.involved is not None],
    }
    actors = [brief(c) for c in comps if getattr(c, "is_actor", False)]
    if actors:
        out["actors"] = actors
    return out


# ---------------------------------------------------------------- operations


def create_capability(
    model,
    name: str,
    layer_name: str | None = None,
    parent: str | None = None,
    description: str | None = None,
):
    if parent:
        par = resolve(model, parent)
        key = require_layer(par)
        if not type_name(par).endswith("Pkg") or not hasattr(par, "capabilities"):
            raise CapError(f"--parent must be a capability package, got {type_name(par)}")
    elif layer_name:
        key = layer_name.lower()
        par = layer(model, key).capability_pkg
    else:
        raise CapError("Give --layer or --parent (a capability package)")
    cap = par.capabilities.create(CAPABILITY_TYPE[key], name=name)
    if description:
        cap.description = description
    return {"created": brief(cap), "parent": brief(par)}


def involve(model, capability: str, elements: list[str]):
    """Involve components/actors/entities, functions or chains in a capability."""
    cap = _capability(model, capability)
    added: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for ref in elements:
        obj = resolve(model, ref)
        same_layer(cap, obj)
        attr, link_type, what = _slot(cap, obj)
        if any(i.involved == obj for i in _links(cap, attr)):
            unchanged.append(brief(obj))
            continue
        getattr(cap, attr).create(link_type, involved=obj)
        added.append({**brief(obj), "as": what})
    return {"capability": brief(cap), "added": added, "unchanged": unchanged}


def uninvolve(model, capability: str, elements: list[str]):
    cap = _capability(model, capability)
    removed = []
    for ref in elements:
        obj = resolve(model, ref)
        attr, _, _ = _slot(cap, obj)
        links = [i for i in _links(cap, attr) if i.involved == obj]
        if not links:
            raise CapError(f"{label(obj)} is not involved in {label(cap)}")
        removed += [{**remove_link(model, link), "involved": brief(obj)} for link in links]
    return {"capability": brief(cap), "removed": removed}


def relate(model, relation: str, capability: str, other: str, remove: bool = False):
    """``capability`` includes / extends / specializes (generalize) ``other``."""
    if relation not in RELATIONS:
        raise CapError(f"Unknown relation {relation!r}; expected {', '.join(RELATIONS)}")
    attr, link_type, ref_attr = RELATIONS[relation]
    cap, oth = _capability(model, capability), _capability(model, other)
    same_layer(cap, oth)
    if cap == oth:
        raise CapError("A capability cannot be related to itself")
    links = [i for i in getattr(cap, attr) if getattr(i, ref_attr) == oth]
    if remove:
        if not links:
            raise CapError(f"{label(cap)} does not {relation} {label(oth)}")
        return {"removed": [remove_link(model, link) for link in links]}
    if links:
        return {"unchanged": True, "reason": f"already {relation}s"}
    getattr(cap, attr).create(link_type, **{ref_attr: oth})
    return {"capability": brief(cap), relation: brief(oth)}


def show_capability(model, capability: str) -> dict[str, Any]:
    cap = _capability(model, capability)
    key = require_layer(cap)
    d: dict[str, Any] = with_status({**brief(cap), "layer": key, "parent": brief(cap.parent)}, cap)
    for plain in ("description", "summary"):
        if getattr(cap, plain, None):
            d[plain] = str(getattr(cap, plain))
    for cond in ("precondition", "postcondition"):
        c = getattr(cap, cond, None)
        if c is not None:
            d[cond] = brief(c)
            d[f"{cond}_text"] = constraint_text(c)
    d["involves"] = involved(cap)
    d["realizes"] = [brief(c) for c in cap.realized_capabilities]
    d["realized_by"] = [brief(c) for c in cap.realizing_capabilities]
    d["includes"] = [brief(i.included) for i in cap.includes]
    d["included_by"] = [brief(i.parent) for i in cap.included_by]
    d["extends"] = [brief(i.extended) for i in cap.extends]
    d["extended_by"] = [brief(i.parent) for i in cap.extended_by]
    d["specializes"] = [brief(i.super) for i in cap.generalizations]
    d["specialized_by"] = [brief(i.parent) for i in cap.generalized_by]
    if key == "sa":
        d["exploited_by_missions"] = [
            brief(m) for m in model.sa.all_missions if cap in m.exploits
        ]
    d["scenarios"] = [brief(s) for s in cap.scenarios]
    d["diagrams"] = [{"uuid": dg.uuid, "name": dg.name} for dg in cap.visible_on_diagrams]
    d["issues"] = _issues(model, cap, key, d)
    return d


def _issues(model, cap, key, d) -> list[str]:
    inv = d["involves"]
    issues = []
    if not inv["functions"] and not inv["chains"]:
        issues.append("no functions or chains are involved")
    if not inv.get("components", inv.get("entities")) and not inv.get("actors"):
        issues.append(f"no {'entities' if key == 'oa' else 'components or actors'} are involved")
    if key != "oa" and not d["realizes"]:
        upper = {"sa": "oa", "la": "sa", "pa": "la"}[key]
        if len(layer(model, upper).all_capabilities):
            issues.append(f"does not realize any {upper} capability")
    if key != "oa":
        comps = {c["uuid"] for c in inv.get("components", []) + inv.get("actors", [])}
        for f in inv["functions"]:
            owner = getattr(model.by_uuid(f["uuid"]), "owner", None)
            if owner is not None and getattr(owner, "uuid", None) not in comps:
                issues.append(
                    f"function {f['name']!r} is allocated to {owner.name!r}, "
                    "which is not involved in the capability"
                )
    return issues


def set_conditions(model, element: str, pre: str | None = None, post: str | None = None,
                   clear_pre: bool = False, clear_post: bool = False):
    """Pre-/postcondition text of a capability or functional chain."""
    obj = resolve(model, element)
    if not (is_capability(obj) or type_name(obj) in CHAIN_TYPES):
        raise CapError(f"Pre/postconditions belong to capabilities and functional chains, not {type_name(obj)}")
    if pre is None and post is None and not clear_pre and not clear_post:
        raise CapError("Give --pre and/or --post text (or --clear-pre / --clear-post)")
    out: dict[str, Any] = {"element": brief(obj)}
    for text, clear, xml_attr, accessor in ((pre, clear_pre, "preCondition", "precondition"),
                                            (post, clear_post, "postCondition", "postcondition")):
        if text is not None or clear:
            set_constraint(model, obj, xml_attr, accessor, None if clear else text)
            out[accessor] = None if clear else text
    return out


# ------------------------------------------------------------------ missions


def create_mission(model, name: str, description: str | None = None):
    pkgs = list(model.sa.mission_pkg)
    if not pkgs:
        raise CapError("The model has no mission package in System Analysis")
    mi = pkgs[0].missions.create("Mission", name=name)
    if description:
        mi.description = description
    return {"created": brief(mi), "parent": brief(pkgs[0])}


def _mission(model, ref):
    obj = resolve(model, ref)
    if type_name(obj) != "Mission":
        raise CapError(f"Expected a mission, got {type_name(obj)} {obj.uuid}")
    return obj


def mission_exploit(model, mission: str, capability: str, remove: bool = False):
    mi, cap = _mission(model, mission), _capability(model, capability)
    if require_layer(cap) != "sa":
        raise CapError("Missions exploit System Analysis capabilities only")
    links = [x for x in mi.capability_exploitations if x.capability == cap]
    if remove:
        if not links:
            raise CapError(f"{label(mi)} does not exploit {label(cap)}")
        return {"removed": [remove_link(model, x) for x in links]}
    if links:
        return {"unchanged": True, "reason": "already exploited"}
    mi.capability_exploitations.create("CapabilityExploitation", capability=cap)
    return {"mission": brief(mi), "exploits": brief(cap)}


def mission_involve(model, mission: str, elements: list[str], remove: bool = False):
    mi = _mission(model, mission)
    changed = []
    for ref in elements:
        obj = resolve(model, ref)
        if type_name(obj) != "SystemComponent":
            raise CapError(f"Missions involve SA actors or the system, not {type_name(obj)}")
        links = [i for i in mi.involvements if i.involved == obj]
        if remove:
            if not links:
                raise CapError(f"{label(obj)} is not involved in {label(mi)}")
            changed += [remove_link(model, x) for x in links]
        elif not links:
            mi.involvements.create("MissionInvolvement", involved=obj)
            changed.append(brief(obj))
    return {"mission": brief(mi), "removed" if remove else "added": changed}


def show_mission(model, mission: str):
    mi = _mission(model, mission)
    return with_status({
        **brief(mi),
        "layer": "sa",
        "exploits": [brief(x.capability) for x in mi.capability_exploitations],
        "involves": [brief(i.involved) for i in mi.involvements],
    }, mi)


OPS: dict[str, Callable[..., Any]] = {
    "create-capability": create_capability,
    "capability-involve": involve,
    "capability-uninvolve": uninvolve,
    "capability-include": lambda model, capability, other, remove=False: relate(model, "include", capability, other, remove),
    "capability-extend": lambda model, capability, other, remove=False: relate(model, "extend", capability, other, remove),
    "capability-generalize": lambda model, capability, other, remove=False: relate(model, "generalize", capability, other, remove),
    "set-conditions": set_conditions,
    "create-mission": create_mission,
    "mission-exploit": mission_exploit,
    "mission-involve": mission_involve,
}
