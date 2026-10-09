"""Model loading, element lookup and JSON serialization helpers."""

from __future__ import annotations

import os
import pathlib
import re
import warnings
from typing import Any

import capellambse

LAYERS = ("oa", "sa", "la", "pa")

LAYER_CLASS = {
    "OperationalAnalysis": "oa",
    "SystemAnalysis": "sa",
    "LogicalArchitecture": "la",
    "PhysicalArchitecture": "pa",
}

# Concrete metaclasses per layer. capellambse's ``create()`` does not always
# infer the right type (e.g. it creates a LogicalFunction below an
# OperationalActivity), so we always pass these explicitly.
FUNCTION_TYPE = {
    "oa": "OperationalActivity",
    "sa": "SystemFunction",
    "la": "LogicalFunction",
    "pa": "PhysicalFunction",
}
COMPONENT_TYPE = {
    "oa": "Entity",
    "sa": "SystemComponent",
    "la": "LogicalComponent",
    "pa": "PhysicalComponent",
}

UUID_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


class CapError(Exception):
    """A user-facing error; printed as JSON, exits with status 1."""


def find_model(path: str | None) -> pathlib.Path:
    """Resolve the .aird entrypoint from an option, $CAPELLA_MODEL or cwd."""
    path = path or os.environ.get("CAPELLA_MODEL")
    if path:
        p = pathlib.Path(path)
        if p.is_dir():
            return _single_aird(p)
        if not p.exists():
            raise CapError(f"Model not found: {p}")
        return p
    return _single_aird(pathlib.Path.cwd())


def _single_aird(root: pathlib.Path) -> pathlib.Path:
    found = [
        p
        for p in root.rglob("*.aird")
        if not any(part.startswith(".") for part in p.relative_to(root).parts)
    ]
    if len(found) == 1:
        return found[0]
    if not found:
        raise CapError(
            f"No .aird file found under {root}; pass --model or set CAPELLA_MODEL"
        )
    raise CapError(
        "Several .aird files found; pass --model or set CAPELLA_MODEL: "
        + ", ".join(str(p) for p in found)
    )


def load(path: pathlib.Path) -> capellambse.MelodyModel:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return capellambse.MelodyModel(str(path))


def layer(model, key: str):
    key = key.lower()
    if key not in LAYERS:
        raise CapError(f"Unknown layer {key!r}; expected one of {', '.join(LAYERS)}")
    return getattr(model, key)


def layer_key(obj) -> str | None:
    lay = getattr(obj, "layer", None)
    if lay is None:
        return None
    return LAYER_CLASS.get(type(lay).__name__)


def root_function(model, key: str):
    key = key.lower()
    lay = layer(model, key)
    if key != "oa":
        return lay.root_function
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return lay.root_activity


def root_component(model, key: str):
    """The top-level component; in OA, the package holding top-level entities.

    capellambse's ``root_component`` raises ``MultipleMatchesError`` as soon as
    the Structure package holds a second non-actor component, which would make
    every command touching the root (``check`` included) fail. Capella treats
    the first one as the system, so that one is returned; ``check`` reports
    the others (``ops.structure_violations``).
    """
    key = key.lower()
    lay = layer(model, key)
    if key == "oa":
        return lay.entity_pkg
    tops = top_components(lay)
    return tops[0] if tops else None


def top_components(lay) -> list:
    """Non-actor components directly in a layer's Structure package."""
    return [c for c in lay.component_pkg.components if not getattr(c, "is_actor", False)]


def structure_pkg(model, key: str):
    """The layer's Structure package, where actors live (entities in OA)."""
    key = key.lower()
    lay = layer(model, key)
    return lay.entity_pkg if key == "oa" else lay.component_pkg


def resolve(model, ref: str):
    """Resolve an element reference.

    Accepts a UUID, or a shortcut ``<layer>:root-function`` /
    ``<layer>:root-component`` / ``<layer>:structure`` (actors' package) /
    ``<layer>:functions`` / ``:capabilities`` / ``:data`` / ``:interfaces``
    (the layer's root packages), e.g. ``la:root-function``.
    """
    if ":" in ref:
        lay, _, what = ref.partition(":")
        lay, what = lay.lower(), what.lower()
        if what in ("root-function", "root-activity"):
            return root_function(model, lay)
        if what in ("root-component", "root-entity"):
            return root_component(model, lay)
        if what == "structure":
            return structure_pkg(model, lay)
        pkg_attr = {"functions": "function_pkg", "capabilities": "capability_pkg",
                    "data": "data_pkg", "interfaces": "interface_pkg"}.get(what)
        if pkg_attr:
            return getattr(layer(model, lay), pkg_attr)
        raise CapError(
            f"Unknown shortcut {ref!r}; known: <layer>:root-function, :root-component, "
            ":structure, :functions, :capabilities, :data, :interfaces"
        )
    if not UUID_RE.match(ref):
        raise CapError(f"Not a UUID: {ref!r} (use `capcli search` to find one)")
    try:
        return model.by_uuid(ref)
    except (KeyError, ValueError):
        raise CapError(f"No element with UUID {ref}") from None


def type_name(obj) -> str:
    return type(obj).__name__


def brief(obj) -> dict[str, Any] | None:
    if obj is None:
        return None
    d: dict[str, Any] = {"uuid": obj.uuid, "type": type_name(obj)}
    name = getattr(obj, "name", None)
    if name is not None:
        d["name"] = name
    return d


def is_element(value) -> bool:
    return hasattr(value, "uuid") and hasattr(value, "_element")


def is_list(value) -> bool:
    return hasattr(value, "by_uuid") and hasattr(value, "__iter__")


def to_json(value, limit: int = 200):
    """Convert a capellambse attribute value into JSON-serializable data."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if is_element(value):
        return brief(value)
    if is_list(value):
        items = list(value)
        out = [brief(i) if is_element(i) else to_json(i) for i in items[:limit]]
        if len(items) > limit:
            out.append({"truncated": len(items) - limit})
        return out
    if hasattr(value, "name") and hasattr(value, "value"):  # enum
        return value.name
    return str(value)


# Relations shown by `capcli show` when present on the element.
SHOW_ATTRS = (
    "parent",
    "owner",
    "functions",
    "components",
    "entities",
    "activities",
    "allocated_functions",
    "realized_functions",
    "realizing_functions",
    "realized_components",
    "realizing_components",
    "inputs",
    "outputs",
    "ports",
    "source",
    "target",
    "component_exchanges",
    "allocated_functional_exchanges",
    "involved_functions",
    "involved_components",
    "deployed_components",
    "realized_chains",
    "realizing_chains",
    "links",  # physical link categories
)


def detail(obj, attrs: list[str] | None = None) -> dict[str, Any]:
    d = brief(obj) or {}
    d["layer"] = layer_key(obj)
    for plain in ("description", "summary"):
        v = getattr(obj, plain, None)
        if v:
            d[plain] = str(v)
    if attrs is None:
        with_status(d, obj)
    for attr in attrs or SHOW_ATTRS:
        try:
            v = getattr(obj, attr)
        except AttributeError:
            if attrs:
                raise CapError(f"{type_name(obj)} has no attribute {attr!r}") from None
            continue
        except Exception as e:  # noqa: BLE001 - capellambse may raise anything
            d[attr] = {"error": str(e)}
            continue
        if attrs is None and (v is None or v == "" or (is_list(v) and not len(v))):
            continue
        if attrs is None and attr == "owner":
            # For functions `owner` is the component they are allocated to;
            # for other elements it is a deprecated alias of `parent`.
            if type_name(obj) in FUNCTION_TYPE.values():
                d["allocated_to"] = to_json(v)
            continue
        # Don't let an attribute such as ExchangeItem.type clobber the
        # metaclass/identity keys from brief().
        d[f"attr_{attr}" if attr in ("uuid", "type", "layer") else attr] = to_json(v)
    if attrs is None and hasattr(obj, "inputs") and hasattr(obj, "outputs"):
        d["incoming_exchanges"] = [_exchange(x) for p in obj.inputs for x in p.exchanges]
        d["outgoing_exchanges"] = [_exchange(x) for p in obj.outputs for x in p.exchanges]
    if attrs is None and (
        type_name(obj) in FUNCTION_TYPE.values() or type_name(obj) == "FunctionalExchange"
    ):
        from .chains import chains_involving

        d["chains"] = chains_involving(obj._model, obj)
    if attrs is None and hasattr(obj, "visible_on_diagrams"):
        d["diagrams"] = [{"uuid": dg.uuid, "name": dg.name} for dg in obj.visible_on_diagrams]
    return d


def _exchange(x) -> dict[str, Any]:
    d = brief(x) or {}
    d["from"] = brief(endpoint_owner(x.source))
    d["to"] = brief(endpoint_owner(x.target))
    return d


def require_layer(obj) -> str:
    key = layer_key(obj)
    if key is None:
        raise CapError(f"{type_name(obj)} {obj.uuid} is not inside an architecture layer")
    return key


def is_function(obj) -> bool:
    return type_name(obj) in FUNCTION_TYPE.values()


def is_component(obj) -> bool:
    return type_name(obj) in COMPONENT_TYPE.values()


def same_layer(a, b) -> str:
    ka, kb = require_layer(a), require_layer(b)
    if ka != kb:
        raise CapError(
            f"{brief(a)} is in {ka} but {brief(b)} is in {kb}; "
            "links between elements (exchanges, allocations, involvements, "
            "relations) must stay within one layer "
            "(use `realize` for cross-layer traceability)"
        )
    return ka


XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
DATAVALUE_URI = "http://www.polarsys.org/capella/core/information/datavalue/{VERSION}"
DATAVALUE_ALIAS = "org.polarsys.capella.core.data.information.datavalue"


def datavalue_alias(model, el) -> str:
    """Namespace prefix for datavalue metaclasses in ``el``'s file (added if missing)."""
    _, frag = model._loader._find_fragment(el)
    version = model.info.capella_version or "7.0.0"
    return frag.add_namespace(DATAVALUE_URI.format(VERSION=version), DATAVALUE_ALIAS)


CS_URI = "http://www.polarsys.org/capella/core/cs/{VERSION}"
CS_ALIAS = "org.polarsys.capella.core.data.cs"


def cs_alias(model, el) -> str:
    """Namespace prefix for cs metaclasses (interfaces) in ``el``'s file (added if missing)."""
    _, frag = model._loader._find_fragment(el)
    version = model.info.capella_version or "7.0.0"
    return frag.add_namespace(CS_URI.format(VERSION=version), CS_ALIAS)


def add_xml_child(model, parent_el, tag: str, xtype: str, **attrs: str):
    """Create a child element with a fresh id, registered in the id cache.

    For elements capellambse cannot create through its API (multiplicities,
    constraint specifications). ``xtype`` is the full ``prefix:Metaclass``.
    """
    from lxml import etree

    loader = model._loader
    # new_uuid() checks on exit that the id is used, so index inside it.
    with loader.new_uuid(parent_el) as uid:
        child = etree.SubElement(parent_el, tag)
        child.set(XSI_TYPE, xtype)
        child.set("id", uid)
        for k, v in attrs.items():
            child.set(k, v)
        loader.idcache_index(child)
    return child


def set_constraint(model, owner, xml_attr: str, accessor: str, text: str | None) -> None:
    """Set (or clear, with None) a text constraint such as a guard or precondition.

    Capella stores it as a ``Constraint`` owned by the element and referenced
    by ``xml_attr`` (``guard``, ``preCondition``, ``postCondition``), with an
    ``OpaqueExpression`` specification. capellambse creates the constraint
    without the specification and can't add one, so it is written here.
    """
    from lxml import etree

    old = (owner._element.get(xml_attr) or "").rpartition("#")[2]
    for c in list(owner.constraints):
        if old and c.uuid == old:
            model._loader.idcache_remove(c._element)
            c._element.getparent().remove(c._element)
    if xml_attr in owner._element.attrib:
        del owner._element.attrib[xml_attr]
    if text is None:
        return
    c = owner.constraints.create("Constraint", name="")
    alias = datavalue_alias(model, c._element)
    spec = add_xml_child(model, c._element, "ownedSpecification", f"{alias}:OpaqueExpression")
    etree.SubElement(spec, "bodies").text = text
    etree.SubElement(spec, "languages").text = "capella:linkedText"
    setattr(owner, accessor, c)


def constraint_text(constraint) -> str | None:
    if constraint is None:
        return None
    spec = constraint._element.find("ownedSpecification")
    body = spec.find("bodies") if spec is not None else None
    return body.text if body is not None and body.text is not None else ""


NOT_SET = "NOT_SET"


def status_name(obj) -> str:
    """The element's progress status (see status.py), or NOT_SET."""
    ref = obj._element.get("status")
    if not ref:
        return NOT_SET
    try:
        return obj._model._loader[ref.rpartition("#")[2]].get("name") or NOT_SET
    except KeyError:
        return NOT_SET


def with_status(d: dict[str, Any], obj) -> dict[str, Any]:
    """Add a ``status`` key to a show-style dict when the element has one."""
    name = status_name(obj)
    if name != NOT_SET:
        d["status"] = name
    return d


def ancestors(obj):
    """Proper ancestors of obj inside its layer, nearest first."""
    p = getattr(obj, "parent", None)
    while p is not None and layer_key(p) is not None:
        yield p
        p = getattr(p, "parent", None)


def common_owner(a, b, pred, fallback):
    """Deepest common proper ancestor of a and b satisfying pred.

    This is where Capella puts an exchange or link between a and b.
    """
    b_anc = {x.uuid for x in ancestors(b)}
    for x in ancestors(a):
        if pred(x) and x.uuid in b_anc:
            return x
    return fallback


def endpoint_owner(end):
    """Return the function/component owning a port, or the element itself."""
    if end is None:
        return None
    if type_name(end).endswith("Port"):
        return end.parent
    return end
