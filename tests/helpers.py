"""Helpers shared by the test modules (the fixtures live in conftest.py)."""

from __future__ import annotations

from lxml import etree

XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"
CAPELLA_FILE = "Model Test 7.0.capella"


def capella_xml(model):
    """The saved .capella file of the test model copy, parsed with lxml."""
    return etree.parse(str(model / CAPELLA_FILE))


def by_id(tree, uuid):
    return next(e for e in tree.iter() if isinstance(e.tag, str) and e.get("id") == uuid)


def created_ids(res) -> dict[str, str]:
    """Alias -> UUID for the steps of a batch result that used ``"as"``."""
    return {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}


def ref_ids(value) -> list[str]:
    """The ids of an XML reference list such as ``"#a #b"``, sorted."""
    return sorted(t[1:] for t in (value or "").split())


def named(items, name: str) -> str:
    """UUID of the item called ``name`` in a list/search result (no index picks)."""
    matches = [i["uuid"] for i in items if i.get("name") == name]
    assert len(matches) == 1, f"expected one {name!r}, found {len(matches)}"
    return matches[0]
