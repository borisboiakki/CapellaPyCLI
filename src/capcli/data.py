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

import re
from collections.abc import Callable
from typing import Any

from .model import (
    LAYERS,
    CapError,
    add_xml_child,
    brief,
    datavalue_alias,
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

TYPE_METACLASSES = (
    "Class", "Union", "Collection", "Enumeration", "BooleanType",
    "NumericType", "StringType", "PhysicalQuantity",
)
MECHANISMS = ("UNSET", "FLOW", "OPERATION", "EVENT", "SHARED_DATA")
# Capella leaves aggregationKind out (UNSET) on plain attributes; only
# association ends say ASSOCIATION/AGGREGATION/COMPOSITION.
KINDS = ("UNSET", "ASSOCIATION", "AGGREGATION", "COMPOSITION")

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
            f"{label(typ)} is not a data type; expected one of {', '.join(TYPE_METACLASSES)} "
            "(see `capcli data types`)"
        )
    tl = layer_key(typ)
    if tl is not None and LAYERS.index(tl) > LAYERS.index(element_layer):
        raise CapError(
            f"{label(typ)} is in {tl} and is not visible from {element_layer}: "
            "types can only come from the same layer or a layer above"
        )


def _card(raw) -> str:
    v = str(raw).strip()
    if v == "*":
        return v
    if not re.fullmatch(r"[0-9]+", v):  # not str.isdigit(): it accepts "²" and "٣"
        raise CapError(f"Multiplicity must be a number or '*', got {raw!r}")
    return str(int(v))


def _set_cards(model, obj, min_card, max_card) -> str:
    """Write ownedMinCard / ownedMaxCard the way Capella does (see module doc).

    Returns the multiplicity as written, e.g. ``"0..*"``.
    """
    lo, hi = _card(min_card), _card(max_card)
    if lo == "*":
        raise CapError("The minimum multiplicity cannot be '*'")
    if hi != "*" and int(hi) < int(lo):
        raise CapError(f"Maximum multiplicity {hi} is lower than minimum {lo}")
    el = obj._element
    alias = datavalue_alias(model, el)
    for tag, value in (("ownedMinCard", lo), ("ownedMaxCard", hi)):
        for old in el.findall(tag):
            remove_xml(model, old)
        add_xml_child(model, el, tag, f"{alias}:LiteralNumericValue", value=value)
    return f"{lo}..{hi}"


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
                 kind: str = "UNSET", description: str | None = None):
    owner = resolve(model, cls)
    if type_name(owner) not in ("Class", "Union"):
        raise CapError(f"Properties belong to classes and unions, got {type_name(owner)} {owner.uuid}")
    typ = resolve(model, type)
    _check_type(require_layer(owner), typ)
    kind = kind.upper()
    if kind not in KINDS:
        raise CapError(f"--kind must be one of {', '.join(KINDS)}")
    if any(p.name == name for p in owner.owned_properties):
        raise CapError(f"{label(owner)} already has a property named {name!r}")
    # Capella writes union members as UnionProperty.
    metaclass = "UnionProperty" if type_name(owner) == "Union" else "Property"
    kw = {} if kind == "UNSET" else {"aggregation_kind": kind}
    prop = owner.owned_properties.create(metaclass, name=name, type=typ, **kw)
    mult = _set_cards(model, prop, min, max)
    if description:
        prop.description = description
    return {"created": brief(prop), "class": brief(owner), "type": brief(typ), "multiplicity": mult}


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
            raise CapError(f"{label(enum)} already has a literal {n!r}")
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
    mult = _set_cards(model, el, min, max)
    return {"created": brief(el), "exchange_item": brief(ei), "type": brief(typ), "multiplicity": mult}


def assign(model, exchange_item: str, elements: list[str], remove: bool = False):
    """Make exchanges / function ports carry an exchange item (or stop)."""
    ei = resolve(model, exchange_item)
    if type_name(ei) != "ExchangeItem":
        raise CapError(f"Expected an exchange item, got {type_name(ei)} {ei.uuid}")
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
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
            raise CapError(f"{label(ei)} is in {el} and is not visible from {cl}")
        done = toggle(getattr(carrier, attr), ei, remove, f"{label(carrier)} does not carry {label(ei)}")
        (changed if done else unchanged).append(brief(carrier))
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


def _brief_id(model, uuid):
    """brief() of an element by id, or None for elements capellambse can't load."""
    try:
        return brief(model.by_uuid(uuid))
    except KeyError:
        return None


def _typed_users(model, obj) -> list[dict[str, Any]]:
    users = []
    for el in model._loader.xpath(f"//*[@abstractType='#{obj.uuid}']"):
        if el.get("id") and el.getparent() is not None:
            try:
                u = model.by_uuid(el.get("id"))
            except KeyError:
                continue
            if type_name(u) in ("Property", "UnionProperty", "ExchangeItemElement"):
                users.append({**brief(u), "of": brief(u.parent)})
    return users


def show(model, uuid: str) -> dict[str, Any]:
    obj = resolve(model, uuid)
    kind = type_name(obj)
    d = with_status({**brief(obj), "layer": layer_key(obj), "parent": brief(obj.parent)}, obj)
    if obj.description:
        d["description"] = str(obj.description)
    issues = []
    if kind in ("Class", "Union"):
        d["properties"] = [
            {**brief(p), "type": brief(p.type), "multiplicity": "..".join(c or "?" for c in _cards(p)),
             "kind": getattr(p.aggregation_kind, "name", str(p.aggregation_kind))}
            for p in obj.owned_properties
        ]
        for p in d["properties"]:
            if p["type"] is None:
                issues.append(f"property {p['name']!r} has no type")
        d["used_as_type_by"] = _typed_users(model, obj)
    elif kind == "Collection":
        d["item_type"] = brief(obj.type[0]) if len(obj.type) else None
        d["multiplicity"] = "..".join(c or "?" for c in _cards(obj))
        if d["item_type"] is None:
            issues.append("collection has no item type")
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
            if (b := _brief_id(model, el.get("id"))) is not None:
                carriers.append(b)
        d["carried_by"] = carriers
        if not d["elements"]:
            issues.append("exchange item has no elements")
        if not carriers:
            issues.append("exchange item is not carried by any exchange or port")
    elif kind in TYPE_METACLASSES:
        d["used_as_type_by"] = _typed_users(model, obj)
    else:
        raise CapError(f"{kind} is not a data element; use `capcli show`")
    if kind in GENERALIZABLE:
        d["specializes"] = [brief(g.super) for g in obj.generalizations if g.super is not None]
        subs = [_brief_id(model, el.getparent().get("id"))
                for el in model._loader.xpath(f"//ownedGeneralizations[@super='#{obj.uuid}']")]
        d["specialized_by"] = [b for b in subs if b is not None]
    d["issues"] = issues
    return d


def is_data_element(obj) -> bool:
    return type_name(obj) in TYPE_METACLASSES + ("ExchangeItem",)


# ------------------------------------------------------- more data elements

BASIC_KINDS = {
    "boolean": ("BooleanType", None),
    "integer": ("NumericType", "INTEGER"),
    "float": ("NumericType", "FLOAT"),
    "string": ("StringType", None),
    "physical-quantity": ("PhysicalQuantity", "FLOAT"),
}


def create_type(model, name: str, kind: str, layer_name: str | None = None, parent: str | None = None,
                description: str | None = None):
    """A basic data type: boolean, integer, float, string or physical quantity."""
    k = kind.lower()
    if k not in BASIC_KINDS:
        raise CapError(f"--kind must be one of {', '.join(BASIC_KINDS)}")
    metaclass, num_kind = BASIC_KINDS[k]
    pkg = _parent_pkg(model, layer_name, parent)
    kw = {"name": name}
    if num_kind:
        kw["kind"] = num_kind
    typ = pkg.data_types.create(metaclass, **kw)
    if num_kind == "FLOAT":
        typ._element.set("discrete", "false")  # Capella writes it on every float type
    if metaclass == "BooleanType":
        # Capella gives every boolean type its True/False literals.
        alias = datavalue_alias(model, typ._element)
        add_xml_child(model, typ._element, "ownedLiterals", f"{alias}:LiteralBooleanValue",
                      name="True", abstractType="#" + typ.uuid, value="true")
        add_xml_child(model, typ._element, "ownedLiterals", f"{alias}:LiteralBooleanValue",
                      name="False", abstractType="#" + typ.uuid)
    if description:
        typ.description = description
    return {"created": brief(typ), "parent": brief(pkg), "kind": k}


def create_union(model, name: str, layer_name: str | None = None, parent: str | None = None,
                 description: str | None = None):
    pkg = _parent_pkg(model, layer_name, parent)
    union = pkg.classes.create("Union", name=name)
    if description:
        union.description = description
    return {"created": brief(union), "parent": brief(pkg)}


def create_collection(model, name: str, type: str, layer_name: str | None = None,
                      parent: str | None = None, min: str = "0", max: str = "*",
                      description: str | None = None):
    pkg = _parent_pkg(model, layer_name, parent)
    typ = resolve(model, type)
    _check_type(require_layer(pkg), typ)
    col = pkg.collections.create("Collection", name=name)
    col.type.append(typ)
    mult = _set_cards(model, col, min, max)
    if description:
        col.description = description
    return {"created": brief(col), "parent": brief(pkg), "item_type": brief(typ), "multiplicity": mult}


GENERALIZABLE = ("Class", "Union", "Enumeration", "Collection")


def generalize(model, element: str, super: str, remove: bool = False):
    """``element`` specializes ``super`` (both classes, unions, enumerations or collections)."""
    sub, sup = resolve(model, element), resolve(model, super)
    if type_name(sub) not in GENERALIZABLE or type_name(sup) != type_name(sub):
        raise CapError(f"Generalization links two elements of the same kind among {', '.join(GENERALIZABLE)}")
    if sub == sup:
        raise CapError("An element cannot specialize itself")
    if LAYERS.index(require_layer(sup)) > LAYERS.index(require_layer(sub)):
        raise CapError(f"{label(sup)} is in a layer below {label(sub)} and is not visible from it")
    links = [g for g in sub.generalizations if g.super == sup]
    if remove:
        if not links:
            raise CapError(f"{label(sub)} does not specialize {label(sup)}")
        for g in links:
            remove_xml(model, g._element)
        return {"element": brief(sub), "no_longer_specializes": brief(sup)}
    if links:
        return {"unchanged": True, "reason": "already specializes it"}
    if any(a == sub for a in _supers(sup)):
        raise CapError("This generalization would create a cycle")
    sub.generalizations.create("Generalization", super=sup, sub=sub)
    return {"element": brief(sub), "specializes": brief(sup)}


def _supers(obj, seen=None):
    seen = seen or set()
    for g in obj.generalizations:
        if g.super is not None and g.super.uuid not in seen:
            seen.add(g.super.uuid)
            yield g.super
            yield from _supers(g.super, seen)


OPS: dict[str, Callable[..., Any]] = {
    "create-type": create_type,
    "create-union": create_union,
    "create-collection": create_collection,
    "generalize": generalize,
    "create-class": create_class,
    "add-property": add_property,
    "create-enumeration": create_enumeration,
    "add-literals": add_literals,
    "create-exchange-item": create_exchange_item,
    "add-exchange-item-element": add_element,
    "assign-exchange-item": assign,
}
