"""Modes and states: state machines, regions, states, transitions.

Capella keeps caches and conventions that capellambse 0.8.1 does not:

- every ``Region`` lists its states in ``involvedStates``;
- every ``State`` / ``Mode`` lists the states of its sub-regions in
  ``referencedStates``;
- every ``State`` / ``Mode`` owns a region (named "region"), so it can hold
  sub-states;
- a transition guard is a ``Constraint`` owned by the transition, whose
  ``ownedSpecification`` is an ``OpaqueExpression`` (written by
  ``model.set_constraint``, since capellambse can't).

This module keeps all of that in sync. It also enforces Arcadia's rule that a
region holds either modes or states, never both.
"""

from __future__ import annotations

from typing import Any

from .model import (
    LAYERS,
    CapError,
    constraint_text,
    set_constraint,
    brief,
    is_component,
    is_function,
    layer,
    require_layer,
    resolve,
    same_layer,
    type_name,
    with_status,
)

KINDS = {
    "state": "State",
    "mode": "Mode",
    "initial": "InitialPseudoState",
    "final": "FinalState",
    "terminate": "TerminatePseudoState",
    "choice": "ChoicePseudoState",
    "fork": "ForkPseudoState",
    "join": "JoinPseudoState",
    "shallow-history": "ShallowHistoryPseudoState",
    "deep-history": "DeepHistoryPseudoState",
    "entry-point": "EntryPointPseudoState",
    "exit-point": "ExitPointPseudoState",
}
ACTIVITY_ATTRS = {"entry": "entry", "exit": "exit", "do": "do_activity"}
STATE_TYPES = set(KINDS.values())
HOLDERS = ("State", "Mode")  # the kinds that own sub-regions
TRIGGER_TYPES = ("FunctionalExchange", "ComponentExchange", "ExchangeItem")
AVAILABLE_TYPES = ("FunctionalChain", "OperationalProcess", "OperationalCapability",
                   "Capability", "CapabilityRealization")


def is_mode_element(obj) -> bool:
    return type_name(obj) in STATE_TYPES | {"StateMachine", "Region"}


def _ids(value: str | None) -> list[str]:
    return [t.rpartition("#")[2] for t in (value or "").split()]


def _set_ids(el, attr: str, ids: list[str]) -> None:
    if ids:
        el.set(attr, " ".join("#" + i for i in ids))
    elif attr in el.attrib:
        del el.attrib[attr]


def sync_caches(region) -> None:
    """Rebuild involvedStates of ``region`` and referencedStates of its owner state."""
    el = region._element
    _set_ids(el, "involvedStates", [c.get("id") for c in el if c.tag == "ownedStates"])
    owner = el.getparent()
    if owner is not None and owner.get("id") and type_name(region.parent) in HOLDERS:
        _set_ids(owner, "referencedStates", [
            s.get("id") for r in owner if r.tag == "ownedRegions" for s in r if s.tag == "ownedStates"
        ])


def cache_mismatches(model, fix: bool = False) -> list[dict[str, Any]]:
    """Regions/states whose caches disagree with what they own (for `check`)."""
    out = []
    for region in model.search("Region"):
        el = region._element
        owned = sorted(c.get("id") for c in el if c.tag == "ownedStates")
        bad = sorted(_ids(el.get("involvedStates"))) != owned
        owner = el.getparent()
        if type_name(region.parent) in HOLDERS:
            sub = sorted(s.get("id") for r in owner if r.tag == "ownedRegions" for s in r if s.tag == "ownedStates")
            bad = bad or sorted(_ids(owner.get("referencedStates"))) != sub
        if bad:
            out.append({**brief(region), "problem": "involvedStates/referencedStates out of date"})
            if fix:
                sync_caches(region)
    return out


def _machine_of(obj):
    p = obj
    while p is not None and type_name(p) != "StateMachine":
        p = getattr(p, "parent", None)
    return p


def _region_for(model, parent):
    """The region new states go into, given a machine, region or state."""
    kind = type_name(parent)
    if kind == "Region":
        return parent
    if kind == "StateMachine" or kind in HOLDERS:
        regions = list(parent.regions)
        if not regions:
            regions = [parent.regions.create("Region", name="Default Region" if kind == "StateMachine" else "region")]
        return regions[0]
    raise CapError(f"States go into a state machine, a region or a state/mode, not {kind}")


# ---------------------------------------------------------------- operations


def create_machine(model, owner: str, name: str | None = None):
    own = resolve(model, owner)
    if not (is_component(own) or type_name(own) == "Entity"):
        raise CapError(f"State machines belong to components, actors or entities, not {type_name(own)}")
    require_layer(own)
    sm = own.state_machines.create("StateMachine", name=name or f"{own.name} State Machine")
    region = sm.regions.create("Region", name="Default Region")
    return {"created": brief(sm), "owner": brief(own), "region": brief(region)}


def add_state(model, parent: str, name: str, kind: str = "state", description: str | None = None):
    par = resolve(model, parent)
    k = kind.lower()
    if k not in KINDS:
        raise CapError(f"--kind must be one of {', '.join(KINDS)}")
    region = _region_for(model, par)
    xtype = KINDS[k]
    present = {type_name(s) for s in region.states}
    if xtype in HOLDERS and (present & set(HOLDERS)) - {xtype}:
        raise CapError(
            f"Region {region.name!r} already holds {'modes' if xtype == 'State' else 'states'}; "
            "Arcadia does not mix modes and states in one region"
        )
    if xtype == "InitialPseudoState" and "InitialPseudoState" in present:
        raise CapError(f"Region {region.name!r} already has an initial state")
    st = region.states.create(xtype, name=name)
    if xtype in HOLDERS:
        st.regions.create("Region", name="region")  # as Capella does
    if description:
        st.description = description
    sync_caches(region)
    return {"created": brief(st), "region": brief(region), "machine": brief(_machine_of(region))}


def _common_region(source, target):
    def regions(x):
        out, p = [], x.parent
        while p is not None and type_name(p) != "StateMachine":
            if type_name(p) == "Region":
                out.append(p)
            p = p.parent
        return out
    t_regions = {r.uuid for r in regions(target)}
    for r in regions(source):
        if r.uuid in t_regions:
            return r
    return regions(source)[-1]  # top region of the machine


def add_transition(model, source: str, target: str, triggers: list[str] | None = None,
                   effects: list[str] | None = None, trigger_description: str | None = None,
                   guard: str | None = None, name: str | None = None):
    src, tgt = resolve(model, source), resolve(model, target)
    for x in (src, tgt):
        if type_name(x) not in STATE_TYPES:
            raise CapError(f"Transitions connect states, modes and pseudo-states, not {type_name(x)}")
    if _machine_of(src) != _machine_of(tgt):
        raise CapError("Source and target must be in the same state machine")
    if type_name(src) in ("FinalState", "TerminatePseudoState"):
        raise CapError(f"A {type_name(src)} cannot have outgoing transitions")
    if type_name(tgt) == "InitialPseudoState":
        raise CapError("An initial state cannot have incoming transitions")
    region = _common_region(src, tgt)
    tr = region.transitions.create("StateTransition", source=src, target=tgt)
    if name:
        tr.name = name
    for ref in triggers or []:
        ev = resolve(model, ref)
        if type_name(ev) not in TRIGGER_TYPES:
            raise CapError(f"Triggers are {', '.join(TRIGGER_TYPES)}, not {type_name(ev)}")
        if type_name(ev) == "ExchangeItem":  # items may come from a layer above
            if LAYERS.index(require_layer(ev)) > LAYERS.index(require_layer(src)):
                raise CapError(f"{brief(ev)} is in a layer below and is not visible from this state machine")
        else:
            same_layer(src, ev)
        if ev not in tr.triggers:  # a reference list never holds the same element twice
            tr.triggers.append(ev)
    for ref in effects or []:
        fn = resolve(model, ref)
        if not is_function(fn):
            raise CapError(f"Effects are functions, not {type_name(fn)}")
        same_layer(src, fn)
        if fn not in tr.effect:
            tr.effect.append(fn)
    if trigger_description:
        tr.trigger_description = trigger_description
    if guard:
        set_constraint(model, tr, "guard", "guard", guard)
    return {"created": brief(tr), "from": brief(src), "to": brief(tgt), "region": brief(region)}


def set_activities(model, state: str, entry: list[str] | None = None, exit: list[str] | None = None,
                   do: list[str] | None = None, remove: bool = False):
    """Functions run on entering, leaving or while in a state or mode."""
    st = resolve(model, state)
    if type_name(st) not in HOLDERS:
        raise CapError(f"Entry/exit/do activities belong to states and modes, not {type_name(st)}")
    changed: dict[str, list] = {}
    for key, refs in (("entry", entry), ("exit", exit), ("do", do)):
        lst = getattr(st, ACTIVITY_ATTRS[key])
        for ref in refs or []:
            fn = resolve(model, ref)
            if not is_function(fn):
                raise CapError(f"{key} activities are functions, not {type_name(fn)}")
            same_layer(st, fn)
            if remove:
                if fn not in lst:
                    raise CapError(f"{brief(fn)} is not a {key} activity of {brief(st)}")
                lst.remove(fn)
            elif fn in lst:
                continue
            else:
                lst.append(fn)
            changed.setdefault(key, []).append(brief(fn))
    if not changed:
        return {"unchanged": True, "reason": "nothing to change (give --entry, --exit or --do)"}
    return {"state": brief(st), "removed" if remove else "added": changed}


def _activities(st) -> dict[str, Any]:
    out = {}
    for key, attr in ACTIVITY_ATTRS.items():
        fns = [brief(f) for f in getattr(st, attr)]
        if fns:
            out[key] = fns
    return out


def set_available(model, state: str, elements: list[str], remove: bool = False):
    """Declare functions, chains or capabilities available in a state or mode."""
    st = resolve(model, state)
    if type_name(st) not in HOLDERS:
        raise CapError(f"Elements are available in states or modes, not {type_name(st)}")
    changed, unchanged = [], []
    for ref in elements:
        obj = resolve(model, ref)
        if not (is_function(obj) or type_name(obj) in AVAILABLE_TYPES):
            raise CapError(f"Only functions, chains and capabilities have available states, not {type_name(obj)}")
        same_layer(st, obj)
        lst = obj.available_in_states
        if remove:
            if st not in lst:
                raise CapError(f"{brief(obj)} is not available in {brief(st)}")
            lst.remove(st)
            changed.append(brief(obj))
        elif st in lst:
            unchanged.append(brief(obj))
        else:
            lst.append(st)
            changed.append(brief(obj))
    return {"state": brief(st), "removed" if remove else "available": changed, "unchanged": unchanged}


# --------------------------------------------------------------------- reads


def _available_in(model, st) -> list[dict[str, Any]]:
    return [
        brief(model.by_uuid(el.get("id")))
        for el in model._loader.xpath(f"//*[contains(concat(' ', @availableInStates, ' '), ' #{st.uuid} ')]")
    ]


def _guard_text(tr) -> str | None:
    return constraint_text(tr.guard)


def _transition(tr) -> dict[str, Any]:
    d = {**brief(tr), "from": brief(tr.source), "to": brief(tr.target)}
    if tr.triggers:
        d["triggers"] = [brief(t) for t in tr.triggers]
    if tr.effect:
        d["effects"] = [brief(e) for e in tr.effect]
    if tr.trigger_description:
        d["trigger_description"] = tr.trigger_description
    g = _guard_text(tr)
    if g is not None:
        d["guard"] = g
    return d


def _region_tree(model, region, issues: list[str]) -> dict[str, Any]:
    states = list(region.states)
    trans = list(region.transitions)
    incoming = {t.target.uuid for t in model.search("StateTransition", below=_machine_of(region)) if t.target is not None}
    kinds = {type_name(s) for s in states}
    # Mode machines often have no initial state in Capella; only flag states.
    if len([s for s in states if type_name(s) == "State"]) > 1 and "InitialPseudoState" not in kinds:
        issues.append(f"region {region.name!r} has several states but no initial state")
    out_states = []
    for s in states:
        node = with_status(brief(s), s)
        if type_name(s) in HOLDERS:
            node.update(_activities(s))
            avail = _available_in(model, s)
            if avail:
                node["available"] = avail
            subs = [r for r in s.regions if len(r.states)]
            if subs:
                node["regions"] = [_region_tree(model, r, issues) for r in subs]
            if s.uuid not in incoming and "InitialPseudoState" in kinds:
                issues.append(f"{type_name(s).lower()} {s.name!r} is not reachable (no incoming transition)")
        out_states.append(node)
    return {**brief(region), "states": out_states, "transitions": [_transition(t) for t in trans]}


def show(model, uuid: str) -> dict[str, Any]:
    obj = resolve(model, uuid)
    kind = type_name(obj)
    if kind == "StateMachine":
        issues: list[str] = []
        return with_status({
            **brief(obj), "layer": require_layer(obj), "owner": brief(obj.parent),
            "regions": [_region_tree(model, r, issues) for r in obj.regions],
            "issues": issues,
        }, obj)
    if kind in STATE_TYPES:
        machine = _machine_of(obj)
        d = with_status({**brief(obj), "layer": require_layer(obj), "machine": brief(machine), "region": brief(obj.parent)}, obj)
        d["incoming"] = [_transition(t) for t in model.search("StateTransition", below=machine) if t.target == obj]
        d["outgoing"] = [_transition(t) for t in model.search("StateTransition", below=machine) if t.source == obj]
        if kind in HOLDERS:
            d.update(_activities(obj))
            d["realizes"] = [brief(x) for x in obj.realized_states]
            d["available"] = _available_in(model, obj)
            d["sub_states"] = [brief(s) for r in obj.regions for s in r.states]
        return d
    if kind == "Region":
        return _region_tree(model, obj, [])
    raise CapError(f"{kind} is not a state machine element; use `capcli show`")


def list_machines(model, layer_name: str):
    lay = layer(model, layer_name)
    items = []
    for sm in model.search("StateMachine", below=lay):
        n_states = len(model.search(*STATE_TYPES, below=sm))
        items.append({**brief(sm), "owner": brief(sm.parent), "states": n_states})
    return {"layer": layer_name, "count": len(items), "items": items}


OPS = {
    "create-state-machine": create_machine,
    "add-state": add_state,
    "add-transition": add_transition,
    "set-available": set_available,
    "set-activities": set_activities,
}
