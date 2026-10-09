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
    lay = layer(model, key)
    if key != "oa":
        return lay.root_function
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return lay.root_activity


def root_component(model, key: str):
    """The top-level component; in OA, the package holding top-level entities."""
    lay = layer(model, key)
    return lay.entity_pkg if key == "oa" else lay.root_component


def resolve(model, ref: str):
    """Resolve an element reference.

    Accepts a UUID, or a shortcut ``<layer>:root-function`` /
    ``<layer>:root-component`` (e.g. ``la:root-function``).
    """
    if ":" in ref:
        lay, _, what = ref.partition(":")
        if what in ("root-function", "root-activity"):
            return root_function(model, lay)
        if what in ("root-component", "root-entity"):
            return root_component(model, lay)
        raise CapError(f"Unknown shortcut {ref!r}")
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
)


def detail(obj, attrs: list[str] | None = None) -> dict[str, Any]:
    d = brief(obj) or {}
    d["layer"] = layer_key(obj)
    for plain in ("description", "summary"):
        v = getattr(obj, plain, None)
        if v:
            d[plain] = str(v)
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
        d[attr] = to_json(v)
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
            "exchanges and allocations must stay within one layer "
            "(use `realize` for cross-layer traceability)"
        )
    return ka


def endpoint_owner(end):
    """Return the function/component owning a port, or the element itself."""
    if end is None:
        return None
    if type_name(end).endswith("Port"):
        return end.parent
    return end
