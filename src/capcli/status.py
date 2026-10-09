"""Progress status (DRAFT, TO_BE_REVIEWED, …) of model elements.

Capella stores an element's status as ``status="#<literal id>"``, pointing at
one literal of the project-level ``EnumerationPropertyType`` named
``ProgressStatus``. capellambse's ``progress_status`` is just the name of that
literal, read-only.

capellambse accepts *any* ``EnumerationPropertyLiteral`` as a status,
including PVMT enumeration values, and ``element.status = None`` raises. So
values are looked up here, restricted to ProgressStatus, and clearing
deletes the attribute.
"""

from __future__ import annotations

from .model import NOT_SET, CapError, brief, layer, layer_key, resolve, status_name

PROGRESS_STATUS_TYPE = "ProgressStatus"


def _status_type(model):
    types = [t for t in model.project.enumeration_property_types if t.name == PROGRESS_STATUS_TYPE]
    if not types:
        raise CapError(
            f"The project defines no {PROGRESS_STATUS_TYPE!r} enumeration, so no "
            "status values are available (Capella creates it with new projects)"
        )
    return types[0]


def values(model) -> list[str]:
    return [lit.name for lit in _status_type(model).literals]


def _literal(model, value: str):
    lits = {lit.name.upper(): lit for lit in _status_type(model).literals}
    lit = lits.get(value.strip().upper())
    if lit is None:
        raise CapError(
            f"Unknown status {value!r}; allowed: {', '.join(values(model))} "
            f"(or {NOT_SET} to clear)"
        )
    return lit


def set_status(model, value: str, elements: list[str]):
    """Set (or clear, with NOT_SET) the status of one or more elements."""
    clear = value.strip().upper() == NOT_SET
    lit = None if clear else _literal(model, value)
    objs = [resolve(model, ref) for ref in elements]
    changed, unchanged = [], []
    for obj in objs:
        if not hasattr(type(obj), "status"):
            raise CapError(f"{brief(obj)} has no status")
        before = status_name(obj)
        after = NOT_SET if clear else lit.name
        if before == after:
            unchanged.append(brief(obj))
            continue
        if clear:
            del obj.status
        else:
            obj.status = lit
        changed.append({**brief(obj), "from": before, "to": after})
    return {"status": NOT_SET if clear else lit.name, "changed": changed, "unchanged": unchanged}


def list_by_status(model, value: str | None = None, layer_name: str | None = None):
    """Elements that have a status, optionally filtered by value and layer."""
    wanted = None if value is None else (NOT_SET if value.upper() == NOT_SET else _literal(model, value).name)
    if wanted == NOT_SET:
        raise CapError("Listing NOT_SET elements would return most of the model; filter by a value")
    roots = [layer(model, layer_name)._element] if layer_name else [
        t.root for t in model._loader.trees.values()
    ]
    found: dict[str, list] = {}
    seen = set()
    for root in roots:
        for el in root.iter():
            if not isinstance(el.tag, str) or not el.get("status") or el.get("id") in seen:
                continue
            seen.add(el.get("id"))
            obj = model.by_uuid(el.get("id"))
            name = status_name(obj)
            if wanted and name != wanted:
                continue
            found.setdefault(name, []).append({**brief(obj), "layer": layer_key(obj)})
    return {"count": sum(len(v) for v in found.values()), "by_status": found}


OPS = {"set-status": set_status}
