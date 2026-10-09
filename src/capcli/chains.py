"""Functional chain (operational process in OA) operations.

A chain is a container of *involvements*:

- ``FunctionalChainInvolvementFunction``: a function taking part in the chain;
- ``FunctionalChainInvolvementLink``: a functional exchange, whose ``source``
  and ``target`` point at function involvements of the same chain.

capellambse exposes ``involved_functions`` / ``involved_links`` read-only, so
the involvements are created here directly and kept consistent: adding an
exchange also involves both of its functions, removing a function also
removes the links attached to it.
"""

from __future__ import annotations

from typing import Any

from .model import (LAYERS, CapError, brief, constraint_text, endpoint_owner, layer, resolve, root_function,
                    type_name, with_status)
from .model import is_function as _is_function
from .model import require_layer as _require_layer
from .model import same_layer as _same_layer

CHAIN_KINDS = ("SIMPLE", "COMPOSITE", "FRAGMENT")
CHAIN_TYPES = ("FunctionalChain", "OperationalProcess")
FN_INV = "FunctionalChainInvolvementFunction"
LINK_INV = "FunctionalChainInvolvementLink"


def is_chain(obj) -> bool:
    return type_name(obj) in CHAIN_TYPES


def _chain(model, ref: str):
    obj = resolve(model, ref)
    if not is_chain(obj):
        raise CapError(f"Expected a functional chain, got {type_name(obj)} {obj.uuid}")
    return obj


def all_chains(model, key: str):
    lay = layer(model, key)
    return list(lay.all_operational_processes if key == "oa" else lay.all_functional_chains)


def chains_involving(model, obj) -> list[dict[str, Any]]:
    """Chains in which a function or exchange takes part."""
    key = _require_layer(obj)
    return [brief(c) for c in all_chains(model, key) if obj in c.involved]


def _fn_involvements(chain):
    return [i for i in chain.involvements if type_name(i) == FN_INV]


def _link_involvements(chain):
    return [i for i in chain.involvements if type_name(i) == LINK_INV]


def _fn_involvement(chain, fn, create: bool):
    for inv in _fn_involvements(chain):
        if inv.involved == fn:
            return inv, False
    if not create:
        return None, False
    return chain.involvements.create(FN_INV, involved=fn), True


def _remove(model, obj) -> dict[str, Any]:
    el = obj._element
    info = {"uuid": obj.uuid, "type": type_name(obj)}
    model._loader.idcache_remove(el)
    el.getparent().remove(el)
    return info


def _exchanges_between(model, src, tgt):
    key = _require_layer(src)
    return [
        x
        for x in layer(model, key).all_function_exchanges
        if endpoint_owner(x.source) == src and endpoint_owner(x.target) == tgt
    ]


# ---------------------------------------------------------------- operations


def add_to_chain(model, chain: str, elements: list[str]):
    """Involve functions and/or functional exchanges in a chain."""
    ch = _chain(model, chain)
    added, unchanged = [], []
    for ref in elements:
        obj = resolve(model, ref)
        _same_layer(ch, obj)
        if _is_function(obj):
            inv, new = _fn_involvement(ch, obj, create=True)
            (added if new else unchanged).append(brief(obj))
        elif type_name(obj) == "FunctionalExchange":
            if any(i.involved == obj for i in _link_involvements(ch)):
                unchanged.append(brief(obj))
                continue
            src, tgt = endpoint_owner(obj.source), endpoint_owner(obj.target)
            if src is None or tgt is None:
                raise CapError(f"Exchange {obj.uuid} has a missing source or target")
            for fn in (src, tgt):
                _, new = _fn_involvement(ch, fn, create=True)
                if new:
                    added.append(brief(fn))
            s_inv, _ = _fn_involvement(ch, src, create=False)
            t_inv, _ = _fn_involvement(ch, tgt, create=False)
            ch.involvements.create(LINK_INV, involved=obj, source=s_inv, target=t_inv)
            added.append(brief(obj))
        else:
            raise CapError(
                f"Only functions and functional exchanges can be added to a chain, "
                f"not {type_name(obj)}"
            )
    return {"chain": brief(ch), "added": added, "unchanged": unchanged}


def remove_from_chain(model, chain: str, elements: list[str]):
    """Remove functions (with their links) or exchanges from a chain.

    The functions and exchanges themselves are kept; only their
    involvement in the chain is removed.
    """
    ch = _chain(model, chain)
    removed = []
    gone: set[str] = set()  # exchanges whose links went with a function removed earlier in this call
    for ref in elements:
        obj = resolve(model, ref)
        if _is_function(obj):
            inv, _ = _fn_involvement(ch, obj, create=False)
            if inv is None:
                raise CapError(f"{brief(obj)} is not part of chain {ch.uuid}")
            for link in _link_involvements(ch):
                if link.source == inv or link.target == inv:
                    gone.add(link.involved.uuid)
                    removed.append({**_remove(model, link), "involved": brief(link.involved)})
            removed.append({**_remove(model, inv), "involved": brief(obj)})
        else:
            links = [i for i in _link_involvements(ch) if i.involved == obj]
            if not links and obj.uuid in gone:
                continue  # already removed with one of its functions
            if not links:
                raise CapError(f"{brief(obj)} is not part of chain {ch.uuid}")
            for link in links:
                removed.append({**_remove(model, link), "involved": brief(obj)})
    return {"chain": brief(ch), "removed": removed}


def create_chain(
    model,
    name: str,
    parent: str | None = None,
    layer_name: str | None = None,
    path: list[str] | None = None,
    elements: list[str] | None = None,
    kind: str | None = None,
    description: str | None = None,
):
    """Create a chain, optionally along a function path and/or with elements.

    ``path`` is a list of functions; consecutive functions are connected by
    the (single) functional exchange between them, which must exist.
    """
    if parent:
        par = resolve(model, parent)
    elif layer_name:
        par = root_function(model, layer_name)
    else:
        raise CapError("Give --parent (a function) or --layer")
    key = _require_layer(par)
    if not hasattr(par, "functional_chains"):
        raise CapError(f"{type_name(par)} cannot own functional chains")

    # Resolve the path before creating anything, so errors leave no trace.
    hops = []
    fns = [resolve(model, r) for r in path or []]
    for fn in fns:
        if not _is_function(fn):
            raise CapError(f"Path entries must be functions, got {type_name(fn)} {fn.uuid}")
        _same_layer(par, fn)
    for a, b in zip(fns, fns[1:]):
        cands = _exchanges_between(model, a, b)
        if not cands:
            raise CapError(
                f"No functional exchange from {brief(a)} to {brief(b)}; create one with "
                "`capcli create function-exchange` first"
            )
        if len(cands) > 1:
            raise CapError(
                f"Several exchanges from {a.name!r} to {b.name!r}: "
                + ", ".join(f"{x.name!r} {x.uuid}" for x in cands)
                + "; create the chain without this hop and `chain add` the exchange you want"
            )
        hops.append(cands[0].uuid)

    kw: dict[str, Any] = {"name": name}
    if kind:
        if kind.upper() not in CHAIN_KINDS:
            raise CapError(f"kind must be one of {', '.join(CHAIN_KINDS)}, not {kind!r}")
        kw["kind"] = kind.upper()
    ch = par.functional_chains.create(
        "OperationalProcess" if key == "oa" else "FunctionalChain", **kw
    )
    if description:
        ch.description = description
    # Functions first so a single-function path still gets involved.
    add_to_chain(model, ch.uuid, [f.uuid for f in fns] + hops + list(elements or []))
    return {"created": brief(ch), "parent": brief(par), **_summary(ch)}


def link_items(model, chain: str, exchange: str, elements: list[str], remove: bool = False):
    """Exchange items carried on one chain link (a subset of the exchange's)."""
    ch = _chain(model, chain)
    ex = resolve(model, exchange)
    links = [i for i in _link_involvements(ch) if i.involved == ex]
    if not links:
        raise CapError(f"{brief(ex)} is not part of chain {ch.uuid}; `chain add` it first")
    link = links[0]
    changed, unchanged = [], []
    for ref in elements:
        ei = resolve(model, ref)
        if type_name(ei) != "ExchangeItem":
            raise CapError(f"Chain links carry exchange items, not {type_name(ei)}")
        if LAYERS.index(_require_layer(ei)) > LAYERS.index(_require_layer(ch)):
            raise CapError(f"{brief(ei)} is in a layer below the chain and is not visible from it; "
                           "use an exchange item of the same layer or a layer above")
        present = ei in link.exchanged_items
        if remove:
            if not present:
                raise CapError(f"{brief(ei)} is not carried on this chain link")
            link.exchanged_items.remove(ei)
            changed.append(brief(ei))
        elif present:
            unchanged.append(brief(ei))
        else:
            link.exchanged_items.append(ei)
            changed.append(brief(ei))
    out = {"chain": brief(ch), "exchange": brief(ex), "removed" if remove else "added": changed, "unchanged": unchanged}
    not_on_exchange = [x["name"] for x in changed if not remove and resolve(model, x["uuid"]) not in ex.exchanged_items]
    if not_on_exchange:
        out["warning"] = f"not carried by the exchange itself: {', '.join(not_on_exchange)} (see `data assign`)"
    return out


def involve_chain(model, chain: str, capability: str):
    """Declare that a capability involves the chain."""
    ch = _chain(model, chain)
    cap = resolve(model, capability)
    if not hasattr(cap, "chain_involvements"):
        raise CapError(f"Expected a capability, got {type_name(cap)} {cap.uuid}")
    _same_layer(ch, cap)
    if any(i.involved == ch for i in cap.chain_involvements):
        return {"unchanged": True, "reason": "already involved"}
    cap.chain_involvements.create("FunctionalChainAbstractCapabilityInvolvement", involved=ch)
    return {"chain": brief(ch), "capability": brief(cap)}


def show_chain(model, chain: str):
    return _summary(_chain(model, chain), full=True)


def _summary(ch, full: bool = False) -> dict[str, Any]:
    fn_invs = _fn_involvements(ch)
    links = _link_involvements(ch)
    issues = []

    edges = []
    for link in links:
        ex, s, t = link.involved, link.source, link.target
        s_fn = getattr(s, "involved", None)
        t_fn = getattr(t, "involved", None)
        if s_fn is None or t_fn is None:
            issues.append(f"link {link.uuid} ({getattr(ex, 'name', '?')!r}) has no source/target involvement")
            continue
        if ex is not None and (endpoint_owner(ex.source) != s_fn or endpoint_owner(ex.target) != t_fn):
            issues.append(
                f"exchange {ex.name!r} {ex.uuid} does not connect {s_fn.name!r} -> {t_fn.name!r} "
                "as the chain claims"
            )
        edges.append((s_fn, ex, t_fn))

    fns = [i.involved for i in fn_invs]
    has_in = {t.uuid for _, _, t in edges}
    has_out = {s.uuid for s, _, _ in edges}
    if not fns:
        issues.append("chain is empty")
    elif len(fns) > 1:
        isolated = [f for f in fns if f.uuid not in has_in | has_out]
        for f in isolated:
            issues.append(f"function {f.name!r} {f.uuid} is not connected by any exchange")
        if _components(fns, edges) > 1 and not isolated:
            issues.append("chain is split into several disconnected parts")

    out: dict[str, Any] = {
        "kind": str(getattr(ch.kind, "name", ch.kind)),
        "entry": [brief(f) for f in fns if f.uuid not in has_in],
        "exit": [brief(f) for f in fns if f.uuid not in has_out],
        "steps": [
            {"from": brief(s), "exchange": brief(x), "to": brief(t), **_items_on(links, x)} for s, x, t in _ordered(fns, edges)
        ],
        "issues": issues,
    }
    if full:
        head = with_status(brief(ch) or {}, ch)
        head["layer"] = _require_layer(ch)
        head["parent"] = brief(ch.parent)
        if ch.description:
            head["description"] = str(ch.description)
        head["functions"] = [brief(f) for f in fns]
        for cond in ("precondition", "postcondition"):
            c = getattr(ch, cond, None)
            if c is not None:  # same keys as a capability's show
                head[cond] = brief(c)
                head[f"{cond}_text"] = constraint_text(c)
        head["realized_chains"] = [brief(c) for c in ch.realized_chains]
        head["realizing_chains"] = [brief(c) for c in ch.realizing_chains]
        return {**head, **out}
    return out


def _items_on(links, ex) -> dict[str, Any]:
    items = [brief(ei) for link in links if link.involved == ex for ei in link.exchanged_items]
    return {"exchange_items": items} if items else {}


def _components(fns, edges) -> int:
    parent = {f.uuid: f.uuid for f in fns}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s, _, t in edges:
        if s.uuid in parent and t.uuid in parent:
            parent[find(s.uuid)] = find(t.uuid)
    return len({find(f.uuid) for f in fns})


def _ordered(fns, edges):
    """Edges in breadth-first order from the entry functions."""
    has_in = {t.uuid for _, _, t in edges}
    queue = [f.uuid for f in fns if f.uuid not in has_in] or [f.uuid for f in fns[:1]]
    seen_nodes, seen_edges, out = set(queue), set(), []
    while queue:
        cur = queue.pop(0)
        for i, (s, x, t) in enumerate(edges):
            if s.uuid == cur and i not in seen_edges:
                seen_edges.add(i)
                out.append((s, x, t))
                if t.uuid not in seen_nodes:
                    seen_nodes.add(t.uuid)
                    queue.append(t.uuid)
    # Cycles or parts not reachable from an entry point.
    out += [e for i, e in enumerate(edges) if i not in seen_edges]
    return out


OPS = {
    "create-chain": create_chain,
    "chain-add": add_to_chain,
    "chain-remove": remove_from_chain,
    "involve-chain": involve_chain,
    "chain-link-items": link_items,
}
