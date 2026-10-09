"""Data model: classes, properties, enumerations, exchange items.

What capellambse 0.8.1 writes differs from Capella here, so this module
fills the gaps at the XML level:

- properties and exchange item elements get no ``ownedMinCard`` /
  ``ownedMaxCard`` (Capella writes 1..1 by default), and the cards can't be
  created through the API ("Cannot create object from a single attribute");
- enumeration literals miss their ``abstractType`` back-reference to the
  enumeration;
- exchange item elements miss ``direction="UNSET"`` and ``composite="true"``.

Types are visible downwards: an LA class may be typed by an SA class or by the
SA "Predefined Types" (Boolean, Integer, String, …), but not the reverse.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from .model import (
    LAYERS,
    CapError,
    brief,
    layer,
    layer_key,
    require_layer,
    resolve,
    type_name,
    with_status,
)

XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
DATAVALUE_URI = "http://www.polarsys.org/capella/core/information/datavalue/{VERSION}"
DATAVALUE_ALIAS = "org.polarsys.capella.core.data.information.datavalue"

TYPE_METACLASSES = (
    "Class", "Union", "Collection", "Enumeration", "BooleanType",
    "NumericType", "StringType", "PhysicalQuantity",
)
MECHANISMS = ("UNSET", "FLOW", "OPERATION", "EVENT", "SHARED_DATA")
KINDS = ("ASSOCIATION", "AGGREGATION", "COMPOSITION")

# Attributes that carry exchange items, per carrier metaclass.
CARRIER_ATTR = {
    "FunctionalExchange": "exchanged_items",
    "FunctionInputPort": "exchange_items",
    "FunctionOutputPort": "exchange_items",
    "ComponentExchange": "convoyed_informations",
}


def _is_data_pkg(obj) -> bool:
    return type_name(obj) == "DataPkg"


def _parent_pkg(model, layer_name: str | None, parent: str | None):
    if parent:
        par = resolve(model, parent)
        if not _is_data_pkg(par):
            raise CapError(
                f"--parent must be a data package (DataPkg), got {type_name(par)}; "
                "or use --layer for the layer's root data package"
            )
        return par
    if layer_name:
        return layer(model, layer_name).data_pkg
    raise CapError("Give --layer or --parent (a data package)")


def _check_type(element_layer: str, typ) -> None:
    if type_name(typ) not in TYPE_METACLASSES:
        raise CapError(
            f"{brief(typ)} is not a data type; expected one of {', '.join(TYPE_METACLASSES)} "
            "(see `capcli data types`)"
        )
    tl = layer_key(typ)
    if tl is not None and LAYERS.index(tl) > LAYERS.index(element_layer):
        raise CapError(
            f"{brief(typ)} is in {tl} and is not visible from {element_layer}: "
            "types can only come from the same layer or a layer above"
        )


def _card(raw) -> str:
    v = str(raw).strip()
    if v != "*" and not v.isdigit():
        raise CapError(f"Multiplicity must be a number or '*', got {raw!r}")
    return v


def _set_cards(model, obj, min_card, max_card) -> None:
    """Write ownedMinCard / ownedMaxCard the way Capella does (see module doc)."""
    lo, hi = _card(min_card), _card(max_card)
    if lo == "*":
        raise CapError("The minimum multiplicity cannot be '*'")
    if hi != "*" and int(hi) < int(lo):
        raise CapError(f"Maximum multiplicity {hi} is lower than minimum {lo}")
    el = obj._element
    loader = model._loader
    alias = _datavalue_alias(model, el)
    for tag, value in (("ownedMinCard", lo), ("ownedMaxCard", hi)):
        for old in el.findall(tag):
            loader.idcache_remove(old)
            el.remove(old)
        # new_uuid() checks on exit that the id is used, so index inside it.
        with loader.new_uuid(el) as uid:
            child = etree.SubElement(el, tag)
            child.set(XSI_TYPE, f"{alias}:LiteralNumericValue")
            child.set("id", uid)
            child.set("value", value)
            loader.idcache_index(child)


def _datavalue_alias(model, el) -> str:
    _, frag = model._loader._find_fragment(el)
    version = model.info.capella_version or "7.0.0"
    return frag.add_namespace(DATAVALUE_URI.format(VERSION=version), DATAVALUE_ALIAS)


def _cards(obj) -> tuple[str | None, str | None]:
    lo = obj._element.find("ownedMinCard")
    hi = obj._element.find("ownedMaxCard")
    return (lo.get("value") if lo is not None else None, hi.get("value") if hi is not None else None)


# ---------------------------------------------------------------- operations


def create_class(model, name: str, layer_name: str | None = None, parent: str | None = None,
                 description: str | None = None):
    pkg = _parent_pkg(model, layer_name, parent)
    cls = pkg.classes.create("Class", name=name)
    if description:
        cls.description = description
    return {"created": brief(cls), "parent": brief(pkg)}


def add_property(model, cls: str, name: str, type: str, min: str = "1", max: str = "1",
                 kind: str = "ASSOCIATION", description: str | None = None):
    owner = resolve(model, cls)
    if type_name(owner) != "Class":
        raise CapError(f"Properties belong to classes, got {type_name(owner)} {owner.uuid}")
    typ = resolve(model, type)
    _check_type(require_layer(owner), typ)
    kind = kind.upper()
    if kind not in KINDS:
        raise CapError(f"--kind must be one of {', '.join(KINDS)}")
    if any(p.name == name for p in owner.owned_properties):
        raise CapError(f"{brief(owner)} already has a property named {name!r}")
    prop = owner.owned_properties.create("Property", name=name, type=typ, aggregation_kind=kind)
    _set_cards(model, prop, min, max)
    if description:
        prop.description = description
    return {"created": brief(prop), "class": brief(owner), "type": brief(typ), "multiplicity": f"{min}..{max}"}


def create_enumeration(model, name: str, layer_name: str | None = None, parent: str | None = None,
                       literals: list[str] | None = None, description: str | None = None):
    pkg = _parent_pkg(model, layer_name, parent)
    enum = pkg.data_types.create("Enumeration", name=name)
    if description:
        enum.description = description
    added = _add_literals(enum, literals or [])
    return {"created": brief(enum), "parent": brief(pkg), "literals": added}


def _add_literals(enum, names: list[str]) -> list[str]:
    existing = {lit.name for lit in enum.owned_literals}
    added = []
    for n in names:
        if n in existing:
            raise CapError(f"{brief(enum)} already has a literal {n!r}")
        lit = enum.owned_literals.create("EnumerationLiteral", name=n)
        # Capella writes the back-reference to the enumeration; capellambse doesn't.
        lit._element.set("abstractType", "#" + enum.uuid)
        existing.add(n)
        added.append(n)
    return added


def add_literals(model, enumeration: str, literals: list[str]):
    enum = resolve(model, enumeration)
    if type_name(enum) != "Enumeration":
        raise CapError(f"Expected an enumeration, got {type_name(enum)} {enum.uuid}")
    return {"enumeration": brief(enum), "added": _add_literals(enum, literals)}


def create_exchange_item(model, name: str, layer_name: str | None = None, parent: str | None = None,
                         mechanism: str = "UNSET", description: str | None = None):
    pkg = _parent_pkg(model, layer_name, parent)
    mech = mechanism.upper()
    if mech not in MECHANISMS:
        raise CapError(f"--mechanism must be one of {', '.join(MECHANISMS)}")
    ei = pkg.exchange_items.create("ExchangeItem", name=name, exchange_mechanism=mech)
    if description:
        ei.description = description
    return {"created": brief(ei), "parent": brief(pkg), "mechanism": mech}


def add_element(model, exchange_item: str, name: str, type: str, min: str = "1", max: str = "1"):
    ei = resolve(model, exchange_item)
    if type_name(ei) != "ExchangeItem":
        raise CapError(f"Expected an exchange item, got {type_name(ei)} {ei.uuid}")
    typ = resolve(model, type)
    _check_type(require_layer(ei), typ)
    el = ei.elements.create("ExchangeItemElement", name=name, type=typ)
    # Same defaults as an element created in Capella.
    el._element.set("direction", "UNSET")
    el._element.set("composite", "true")
    _set_cards(model, el, min, max)
    return {"created": brief(el), "exchange_item": brief(ei), "type": brief(typ), "multiplicity": f"{min}..{max}"}


def assign(model, exchange_item: str, elements: list[str], remove: bool = False):
    """Make exchanges / function ports carry an exchange item (or stop)."""
    ei = resolve(model, exchange_item)
    if type_name(ei) != "ExchangeItem":
        raise CapError(f"Expected an exchange item, got {type_name(ei)} {ei.uuid}")
    changed, unchanged = [], []
    for ref in elements:
        carrier = resolve(model, ref)
        attr = CARRIER_ATTR.get(type_name(carrier))
        if attr is None:
            raise CapError(
                f"Exchange items are carried by functional exchanges, function ports and "
                f"component exchanges, not {type_name(carrier)}"
            )
        cl, el = require_layer(carrier), require_layer(ei)
        if LAYERS.index(el) > LAYERS.index(cl):
            raise CapError(f"{brief(ei)} is in {el} and is not visible from {cl}")
        items = getattr(carrier, attr)
        if remove:
            if ei not in items:
                raise CapError(f"{brief(carrier)} does not carry {brief(ei)}")
            items.remove(ei)
            changed.append(brief(carrier))
        elif ei in items:
            unchanged.append(brief(carrier))
        else:
            items.append(ei)
            changed.append(brief(carrier))
    return {"exchange_item": brief(ei), "removed_from" if remove else "assigned_to": changed, "unchanged": unchanged}


# --------------------------------------------------------------------- reads


def types(model, layer_name: str, name: str | None = None):
    """Data types usable from ``layer_name`` (its own and the layers above)."""
    visible = LAYERS[: LAYERS.index(layer_name) + 1]
    out = []
    for typ in model.search(*TYPE_METACLASSES):
        tl = layer_key(typ)
        if tl is not None and tl not in visible:
            continue
        if name and name.lower() not in (typ.name or "").lower():
            continue
        out.append({**brief(typ), "layer": tl})
    return {"layer": layer_name, "count": len(out), "items": out}


def _typed_users(model, obj) -> list[dict[str, Any]]:
    users = []
    for el in model._loader.xpath(f"//*[@abstractType='#{obj.uuid}']"):
        if el.get("id") and el.getparent() is not None:
            try:
                u = model.by_uuid(el.get("id"))
            except KeyError:
                continue
            if type_name(u) in ("Property", "ExchangeItemElement"):
                users.append({**brief(u), "of": brief(u.parent)})
    return users


def show(model, uuid: str) -> dict[str, Any]:
    obj = resolve(model, uuid)
    kind = type_name(obj)
    d = with_status({**brief(obj), "layer": layer_key(obj), "parent": brief(obj.parent)}, obj)
    if obj.description:
        d["description"] = str(obj.description)
    issues = []
    if kind == "Class":
        d["properties"] = [
            {**brief(p), "type": brief(p.type), "multiplicity": "..".join(c or "?" for c in _cards(p)),
             "kind": getattr(p.aggregation_kind, "name", str(p.aggregation_kind))}
            for p in obj.owned_properties
        ]
        for p in d["properties"]:
            if p["type"] is None:
                issues.append(f"property {p['name']!r} has no type")
        d["used_as_type_by"] = _typed_users(model, obj)
    elif kind == "Enumeration":
        d["literals"] = [lit.name for lit in obj.owned_literals]
        if not d["literals"]:
            issues.append("enumeration has no literals")
        d["used_as_type_by"] = _typed_users(model, obj)
    elif kind == "ExchangeItem":
        d["mechanism"] = getattr(obj.exchange_mechanism, "name", str(obj.exchange_mechanism))
        d["elements"] = [
            {**brief(e), "type": brief(e.type), "multiplicity": "..".join(c or "?" for c in _cards(e))}
            for e in obj.elements
        ]
        carriers = []
        for el in model._loader.xpath(
            f"//*[contains(concat(' ', @exchangedItems, ' ', @convoyedInformations, ' ', "
            f"@incomingExchangeItems, ' ', @outgoingExchangeItems, ' '), ' #{obj.uuid} ')]"
        ):
            carriers.append(brief(model.by_uuid(el.get("id"))))
        d["carried_by"] = carriers
        if not d["elements"]:
            issues.append("exchange item has no elements")
        if not carriers:
            issues.append("exchange item is not carried by any exchange or port")
    elif kind in TYPE_METACLASSES:
        d["used_as_type_by"] = _typed_users(model, obj)
    else:
        raise CapError(f"{kind} is not a data element; use `capcli show`")
    d["issues"] = issues
    return d


def is_data_element(obj) -> bool:
    return type_name(obj) in TYPE_METACLASSES + ("ExchangeItem",)


OPS = {
    "create-class": create_class,
    "add-property": add_property,
    "create-enumeration": create_enumeration,
    "add-literals": add_literals,
    "create-exchange-item": create_exchange_item,
    "add-exchange-item-element": add_element,
    "assign-exchange-item": assign,
}
