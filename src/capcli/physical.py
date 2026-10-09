"""Physical architecture: physical ports, links, paths and deployment.

Capella conventions (checked against the test model, which follows them
everywhere):

- physical ports belong to *node* components, and physical links join two
  node ports; links live in the nearest common parent component (or the
  Structure package);
- deployment is a ``PartDeploymentLink`` owned by the host's Part, with
  ``location`` = host Part and ``deployedElement`` = deployed Part (not the
  ``InstanceDeploymentLink`` the capellambse API also offers). A node hosts
  behaviour or node components; a behaviour component may host behaviour
  components; nothing is deployed on a behaviour component if it is a node;
- a physical path is an ordered chain node → link → node → …, stored as
  involvements linked by ``nextInvolvements``. capellambse writes the
  involvements but not that chain, so it is written here.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from typing import Any

from .model import (
    CapError,
    add_xml_child,
    brief,
    cs_alias,
    label,
    remove_xml,
    require_layer,
    resolve,
    root_component,
    toggle,
    type_name,
    with_status,
)


def _component(obj, what="a physical component"):
    if type_name(obj) != "PhysicalComponent":
        raise CapError(f"Expected {what}, got {type_name(obj)} {obj.uuid}")
    return obj


def _nature(comp) -> str:
    return str(getattr(comp.nature, "name", comp.nature))


def _node(obj):
    _component(obj, "a node component")
    if _nature(obj) != "NODE":
        raise CapError(
            f"{label(obj)} is a {_nature(obj).lower()} component; physical ports and links "
            "belong to node components (create them with --nature node)"
        )
    return obj


def _part(model, comp):
    parts = [p for p in model.search("Part", below=model.pa) if p.type == comp]
    if not parts:
        raise CapError(f"{label(comp)} has no Part; it must be placed in the structure first")
    return parts[0]


def is_physical_element(obj) -> bool:
    return type_name(obj) in ("PhysicalLink", "PhysicalPath", "PhysicalPort")


# ---------------------------------------------------------------- operations


def create_port(model, component: str, name: str):
    comp = _node(resolve(model, component))
    port = comp.physical_ports.create("PhysicalPort", name=name)
    return {"created": brief(port), "component": brief(comp)}


def _ancestors(obj):
    p = obj.parent
    while p is not None and type_name(p) in ("PhysicalComponent", "PhysicalComponentPkg"):
        yield p
        p = p.parent


def create_link(model, source: str, target: str, name: str):
    """Link two nodes (or two node ports); missing ports are created."""
    ends = []
    for ref in (source, target):
        obj = resolve(model, ref)
        if type_name(obj) == "PhysicalPort":
            _node(obj.parent)
            ends.append(obj)
        else:
            ends.append(_node(obj))
    comps = [e.parent if type_name(e) == "PhysicalPort" else e for e in ends]
    if comps[0] == comps[1]:
        raise CapError("A physical link joins two different nodes")
    target_anc = {a.uuid for a in _ancestors(comps[1])}
    owner = next((a for a in _ancestors(comps[0]) if a.uuid in target_anc and hasattr(a, "physical_links")),
                 root_component(model, "pa"))
    ports = []
    for e, c in zip(ends, comps, strict=True):
        ports.append(e if type_name(e) == "PhysicalPort" else c.physical_ports.create("PhysicalPort", name=name))
    link = owner.physical_links.create("PhysicalLink", name=name, ends=ports)
    return {"created": brief(link), "owner": brief(owner), "ports": [brief(p) for p in ports]}


def deploy(model, element: str, host: str, remove: bool = False):
    """Deploy a component on a host component (via their Parts)."""
    comp = _component(resolve(model, element))
    hst = _component(resolve(model, host), "a host component")
    if comp == hst:
        raise CapError("A component cannot be deployed on itself")
    if _nature(hst) != "NODE" and _nature(comp) == "NODE":
        raise CapError("A node component can only be deployed on another node, not on a behaviour component")
    if _nature(hst) not in ("NODE", "BEHAVIOR"):
        raise CapError(f"{label(hst)} has nature {_nature(hst)}; set it to node or behavior first")
    hp, cp = _part(model, hst), _part(model, comp)
    links = [dl for dl in hp.deployment_links if dl.deployed_element == cp]
    if remove:
        if not links:
            raise CapError(f"{label(comp)} is not deployed on {label(hst)}")
        for dl in links:
            remove_xml(model, dl._element)
        return {"undeployed": brief(comp), "from": brief(hst)}
    if links:
        return {"unchanged": True, "reason": "already deployed there"}
    if _deployed_under(model, hst, comp):
        raise CapError(f"{label(hst)} is itself deployed on {label(comp)} (directly or not): "
                       "this deployment would create a cycle")
    hp.deployment_links.create("PartDeploymentLink", deployed_element=cp, location=hp)
    return {"deployed": brief(comp), "on": brief(hst)}


def _deployed_under(model, comp, host) -> bool:
    """True if ``comp`` runs on ``host``, directly or through intermediate hosts."""
    hosts_of = _hosts_of(model)
    todo, seen = [comp], set()
    while todo:
        c = todo.pop()
        if c.uuid in seen:
            continue
        seen.add(c.uuid)
        for h in hosts_of.get(c.uuid, []):
            if h == host:
                return True
            todo.append(h)
    return False


def create_path(model, name: str, links: list[str], parent: str | None = None):
    """A path through consecutive physical links (nodes are derived)."""
    lks = [resolve(model, ref) for ref in links]
    for lk in lks:
        if type_name(lk) != "PhysicalLink":
            raise CapError(f"A path is made of physical links, not {type_name(lk)}")
    if not lks:
        raise CapError("A path needs at least one link")
    if len({lk.uuid for lk in lks}) != len(lks):
        raise CapError("A link appears twice in the path; give each link once, in order")
    nodes_of = [[e.parent for e in lk.ends] for lk in lks]
    # Orient the chain: the first node is the end of link 1 not shared with link 2.
    if len(lks) == 1:
        first = nodes_of[0][0]
    else:
        shared = {c.uuid for c in nodes_of[0]} & {c.uuid for c in nodes_of[1]}
        if not shared:
            raise CapError(f"Links {lks[0].name!r} and {lks[1].name!r} do not share a node")
        # Parallel links share both nodes: then start from link 1's first end.
        first = next((c for c in nodes_of[0] if c.uuid not in shared), nodes_of[0][0])
    hops, current = [first], first
    for lk, ends in zip(lks, nodes_of, strict=True):
        if current not in ends:
            raise CapError(f"Link {lk.name!r} does not start at node {current.name!r}; links must be consecutive")
        nxt = ends[1] if ends[0] == current else ends[0]
        hops += [lk, nxt]
        current = nxt
    owner = resolve(model, parent) if parent else root_component(model, "pa")
    _component(owner, "a physical component to own the path")
    path = owner.physical_paths.create("PhysicalPath", name=name)
    items = [h if type_name(h) == "PhysicalLink" else _part(model, h) for h in hops]
    # involved_items.append() skips an item already in the path, which drops
    # the first node of a ring (A, L1, B, L2, A): write one involvement per hop.
    alias = cs_alias(model, path._element)
    invs = [add_xml_child(model, path._element, "ownedPhysicalPathInvolvements",
                          f"{alias}:PhysicalPathInvolvement", involved="#" + item.uuid)
            for item in items]
    for a, b in pairwise(invs):
        a.set("nextInvolvements", "#" + b.get("id"))  # the order, as Capella writes it
    return {"created": brief(path), "owner": brief(owner), "hops": [brief(h) for h in hops]}


def create_category(model, parent: str, name: str):
    """A physical link category, owned by a physical component or package."""
    par = resolve(model, parent)
    if type_name(par) not in ("PhysicalComponent", "PhysicalComponentPkg"):
        raise CapError(f"Link categories live in a physical component or package, not {type_name(par)}")
    cat = par.physical_link_categories.create("PhysicalLinkCategory", name=name)
    return {"created": brief(cat), "parent": brief(par)}


def category_links(model, category: str, links: list[str], remove: bool = False):
    cat = resolve(model, category)
    if type_name(cat) != "PhysicalLinkCategory":
        raise CapError(f"Expected a physical link category, got {type_name(cat)}")
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for ref in links:
        lk = resolve(model, ref)
        if type_name(lk) != "PhysicalLink":
            raise CapError(f"Categories group physical links, not {type_name(lk)}")
        done = toggle(cat.links, lk, remove, f"{label(lk)} is not in {label(cat)}")
        (changed if done else unchanged).append(brief(lk))
    return {"category": brief(cat), "removed" if remove else "added": changed, "unchanged": unchanged}


# --------------------------------------------------------------------- reads


def _hosts_of(model) -> dict[str, list]:
    """Deployed component uuid -> the components it is deployed on (one search)."""
    out: dict[str, list] = {}
    for d in model.search("PartDeploymentLink"):
        if d.deployed_element is not None and d.location is not None:
            out.setdefault(d.deployed_element.type.uuid, []).append(d.location.type)
    return out


def _host_nodes(model, comp, hosts_of=None) -> set[str]:
    """The nodes a component runs on, directly or through software it runs on.

    A component may be deployed on several hosts (redundancy), so this is a
    set; a node is its own host.
    """
    hosts_of = _hosts_of(model) if hosts_of is None else hosts_of
    nodes, todo, seen = set(), [comp], set()
    while todo:
        c = todo.pop()
        if c is None or c.uuid in seen:
            continue
        seen.add(c.uuid)
        if _nature(c) == "NODE":
            nodes.add(c.uuid)
        else:
            todo.extend(hosts_of.get(c.uuid, []))
    return nodes


def _ce_issue(model, ce, end_nodes, where) -> str | None:
    comps = [getattr(e, "parent", None) for e in (ce.source, ce.target)]
    ends = {n.uuid for n in end_nodes}
    hosts_of = _hosts_of(model)
    if any(c is None or not (_host_nodes(model, c, hosts_of) & ends) for c in comps):
        return (f"component exchange {ce.name!r} is allocated to {where} but its components "
                "are not deployed on the nodes at its ends")
    return None


def _path_hops(path) -> list:
    invs = {c.get("id"): c for c in path._element if c.tag == "ownedPhysicalPathInvolvements"}
    targets = {n.rpartition("#")[2] for c in invs.values() for n in (c.get("nextInvolvements") or "").split()}
    start = [i for i in invs if i not in targets]
    order, cur, seen = [], start[0] if start else None, set()
    while cur and cur not in seen:
        seen.add(cur)
        order.append(invs[cur])
        nxt = (invs[cur].get("nextInvolvements") or "").split()
        cur = nxt[0].rpartition("#")[2] if nxt else None
    order += [c for i, c in invs.items() if i not in seen]
    return [path._model.by_uuid(c.get("involved").rpartition("#")[2]) for c in order if c.get("involved")]


def show(model, uuid: str) -> dict[str, Any]:
    obj = resolve(model, uuid)
    kind = type_name(obj)
    d = with_status({**brief(obj), "layer": require_layer(obj), "parent": brief(obj.parent)}, obj)
    issues: list[str] = []
    if kind == "PhysicalLink":
        ends = list(obj.ends)
        d["ends"] = [{"port": brief(p), "component": brief(p.parent)} for p in ends]
        d["allocated_component_exchanges"] = [brief(ce) for ce in obj.allocated_component_exchanges]
        d["paths"] = [brief(p) for p in obj.physical_paths]
        d["categories"] = [brief(c) for c in model.search("PhysicalLinkCategory") if obj in c.links]
        for ce in obj.allocated_component_exchanges:
            if (msg := _ce_issue(model, ce, [p.parent for p in ends], "this link")):
                issues.append(msg)
    elif kind == "PhysicalPath":
        hops = _path_hops(obj)
        d["hops"] = [brief(h.type if type_name(h) == "Part" else h) for h in hops]
        d["allocated_component_exchanges"] = [brief(ce) for ce in obj.allocated_component_exchanges]
        nodes = [h.type for h in hops if type_name(h) == "Part"]
        kinds = [type_name(h) for h in hops]
        if kinds[::2] != ["Part"] * len(kinds[::2]) or kinds[1::2] != ["PhysicalLink"] * len(kinds[1::2]):
            issues.append("path does not alternate node / link / node")
        for ce in obj.allocated_component_exchanges:
            if nodes and (msg := _ce_issue(model, ce, [nodes[0], nodes[-1]], "this path")):
                issues.append(msg)
    elif kind == "PhysicalPort":
        d["links"] = [brief(lk) for lk in obj.links]
        d["allocated_component_ports"] = [brief(p) for p in obj.allocated_component_ports]
        if not d["links"]:
            issues.append("physical port is not connected to any link")
    elif kind == "PhysicalComponent":
        d["nature"] = _nature(obj)
        part = _part(model, obj)
        d["deployed"] = [brief(dl.deployed_element.type) for dl in part.deployment_links if dl.deployed_element is not None]
        hosts = [dl.location.type for dl in model.search("PartDeploymentLink")
                 if dl.deployed_element == part and dl.location is not None]
        d["deployed_on"] = [brief(h) for h in hosts]
        d["physical_ports"] = [{**brief(p), "links": [brief(lk) for lk in p.links]} for p in obj.physical_ports]
        if d["nature"] == "BEHAVIOR" and not hosts and not obj.is_actor:
            issues.append("behaviour component is not deployed on any node")
        for p in d["physical_ports"]:
            if not p["links"]:
                issues.append(f"physical port {p['name']!r} is not connected to any link")
    else:
        raise CapError(f"{kind} is not a physical architecture element; use `capcli show`")
    d["issues"] = issues
    return d


def list_physical(model, what: str):
    kinds = {"links": "PhysicalLink", "paths": "PhysicalPath", "nodes": "PhysicalComponent",
             "behaviors": "PhysicalComponent"}
    items = []
    for obj in model.search(kinds[what], below=model.pa):
        if what == "nodes" and _nature(obj) != "NODE":
            continue
        if what == "behaviors" and _nature(obj) != "BEHAVIOR":
            continue
        entry = brief(obj)
        if what == "links":
            entry["between"] = [brief(e.parent) for e in obj.ends]
        if what == "behaviors":
            part = _part(model, obj)
            entry["deployed_on"] = [brief(dl.location.type) for dl in model.search("PartDeploymentLink")
                                    if dl.deployed_element == part and dl.location is not None]
        items.append(entry)
    return {"kind": what, "count": len(items), "items": items}


OPS: dict[str, Callable[..., Any]] = {
    "create-physical-port": create_port,
    "create-physical-link": create_link,
    "create-physical-path": create_path,
    "deploy": deploy,
    "create-link-category": create_category,
    "link-category-links": category_links,
}
