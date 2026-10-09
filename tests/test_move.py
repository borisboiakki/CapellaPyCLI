"""Packages, moving elements and `repair structure`."""

import json

import capellambse
from lxml import etree


def _ids(res):
    return {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}


def _build(run):
    return _ids(run("batch", input=json.dumps([
        {"op": "create-function", "as": "a", "parent": "la:root-function", "name": "A"},
        {"op": "create-function", "as": "grp", "parent": "la:root-function", "name": "Group"},
        {"op": "create-function", "as": "b", "parent": "$grp", "name": "B"},
        {"op": "create-function-exchange", "as": "x", "source": "$a", "target": "$b", "name": "x"},
        {"op": "create-component", "as": "k1", "parent": "la:root-component", "name": "K1"},
        {"op": "create-component", "as": "sub", "parent": "la:root-component", "name": "Sub"},
        {"op": "create-component", "as": "k2", "parent": "$sub", "name": "K2"},
        {"op": "create-component-exchange", "as": "cx", "source": "$k2", "target": "$k1", "name": "cx"},
    ])))


def test_create_packages_of_each_kind(run):
    expected = {
        "la:functions": "LogicalFunctionPkg", "la:structure": "LogicalComponentPkg",
        "la:capabilities": "CapabilityRealizationPkg", "la:data": "DataPkg",
        "la:interfaces": "InterfacePkg", "sa:structure": "SystemComponentPkg",
        "oa:functions": "OperationalActivityPkg", "pa:root-component": "PhysicalComponentPkg",
        "la:root-function": "LogicalFunctionPkg",
    }
    for parent, metaclass in expected.items():
        assert run("package", "create", "--parent", parent, "--name", "P")["created"]["type"] == metaclass
    data, code = run("package", "create", "--parent", "sa:root-component", "--name", "X", ok=False)
    assert code == 1 and "black box" in data["error"]
    assert run("check")["ok"]


def test_move_function_rehomes_exchange(run):
    ids = _build(run)
    assert run("show", ids["x"], "--attr", "parent")["parent"]["name"] == "Root Logical Function"
    res = run("move", ids["a"], ids["grp"])
    assert res["rehomed"][0]["uuid"] == ids["x"]
    assert run("show", ids["a"], "--attr", "parent")["parent"]["uuid"] == ids["grp"]
    assert run("show", ids["x"], "--attr", "parent")["parent"]["uuid"] == ids["grp"]
    assert run("move", ids["a"], ids["grp"])["unchanged"]
    assert run("check")["ok"]


def test_move_component_moves_part_and_exchange(run, model):
    ids = _build(run)
    res = run("move", ids["k1"], ids["sub"])
    assert res["part_moved"]["name"] == "K1"
    assert run("show", ids["cx"], "--attr", "parent")["parent"]["uuid"] == ids["sub"]
    tree = etree.parse(str(model / "Model Test 7.0.capella"))
    part = next(e for e in tree.iter() if isinstance(e.tag, str) and e.get("abstractType") == "#" + ids["k1"])
    assert part.tag == "ownedFeatures" and part.getparent().get("id") == ids["sub"]
    assert run("check")["ok"]


def test_move_rules(run):
    ids = _build(run)
    actor = run("list", "la", "actors")["items"][0]["uuid"]
    cap = run("list", "la", "capabilities")["items"][0]["uuid"]
    for args, needle in [
        (("move", ids["sub"], ids["k2"]), "descendants"),
        (("move", ids["k1"], "sa:structure"), "within their layer"),
        (("move", actor, ids["sub"]), "Structure package"),
        (("move", ids["a"], ids["k1"]), "function package"),
        (("move", cap, "la:data"), "capability package"),
        (("move", ids["x"], ids["grp"]), "not supported"),
    ]:
        data, code = run(*args, ok=False)
        assert code == 1 and needle in data["error"], data


def test_move_capability_class_and_package(run):
    cap = run("list", "la", "capabilities")["items"][0]["uuid"]
    cap_pkg = run("package", "create", "--parent", "la:capabilities", "--name", "Sub")["created"]["uuid"]
    assert run("move", cap, cap_pkg)["to"]["uuid"] == cap_pkg
    cls = run("data", "class", "create", "--layer", "la", "--name", "Pos")["created"]["uuid"]
    data_pkg = run("package", "create", "--parent", "la:data", "--name", "Types")["created"]["uuid"]
    assert run("move", cls, data_pkg)["to"]["uuid"] == data_pkg
    fpkg = run("package", "create", "--parent", "la:functions", "--name", "F")["created"]["uuid"]
    fpkg2 = run("package", "create", "--parent", "la:functions", "--name", "G")["created"]["uuid"]
    assert run("move", fpkg2, fpkg)["to"]["uuid"] == fpkg
    assert run("check")["ok"]


def test_repair_structure(run, model):
    # The mistakes from a real project, created the way plain capellambse allows.
    m = capellambse.MelodyModel(str(model / "Model Test 7.0.aird"))
    m.sa.root_component.components.create("SystemComponent", name="Flight Control System")
    m.sa.root_component.components.create("SystemComponent", name="Operator", is_actor=True)
    m.la.root_component.components.create("LogicalComponent", name="Pilot", is_actor=True)
    m.save()
    assert len(run("check", ok=False)[0]["structure"]) == 3

    preview = run("--dry-run", "repair", "structure")
    assert {f["moved"]["name"] for f in preview["fixed"]} == {"Operator", "Pilot"} and not preview["saved"]
    assert len(run("check", ok=False)[0]["structure"]) == 3  # nothing saved

    res = run("repair", "structure")
    assert all(f["part_moved"] for f in res["fixed"])
    assert [x["name"] for x in res["needs_decision"]] == ["Flight Control System"]
    remaining = run("check", ok=False)[0]["structure"]
    assert [v["name"] for v in remaining] == ["Flight Control System"]


def test_reorder(run):
    res = run("batch", input=json.dumps([
        {"op": "create-function", "as": "a", "parent": "la:root-function", "name": "A"},
        {"op": "create-function", "as": "b", "parent": "la:root-function", "name": "B"},
        {"op": "create-function", "as": "c", "parent": "la:root-function", "name": "C"},
    ]))
    ids = _ids(res)
    names = lambda: [f["name"] for f in run("show", "la:root-function")["functions"] if f["name"] in "ABC"]  # noqa: E731
    run("reorder", ids["c"], "--before", ids["a"])
    assert names() == ["C", "A", "B"]
    run("reorder", ids["c"], "--last")
    assert names() == ["A", "B", "C"]
    run("reorder", ids["a"], "--after", ids["b"])
    assert names() == ["B", "A", "C"]
    assert run("reorder", ids["a"], "--after", ids["a"])["unchanged"]
    data, code = run("reorder", ids["a"], "--before", "la:root-component", ok=False)
    assert code == 1 and "sibling" in data["error"]
    data, code = run("reorder", ids["a"], "--first", "--last", ok=False)
    assert code == 1 and "exactly one" in data["error"]
    assert run("check")["ok"]
