import json

from lxml import etree


def _xml(model):
    return etree.parse(str(model / "Model Test 7.0.capella"))


def _by_id(tree, uuid):
    return next(el for el in tree.iter() if isinstance(el.tag, str) and el.get("id") == uuid)


def _type(run, layer, name):
    return next(i["uuid"] for i in run("data", "types", "--layer", layer, "--name", name)["items"] if i["name"] == name)


def _build(run):
    integer = _type(run, "la", "Integer")
    fe = run("list", "la", "function-exchanges")["items"][0]["uuid"]
    res = run("batch", input=json.dumps([
        {"op": "create-class", "as": "pos", "layer": "la", "name": "Position"},
        {"op": "add-property", "class": "$pos", "name": "lat", "type": integer},
        {"op": "add-property", "class": "$pos", "name": "history", "type": "$pos", "min": "0", "max": "*", "kind": "composition"},
        {"op": "create-enumeration", "as": "mode", "layer": "la", "name": "NavMode", "literals": ["AUTO", "MANUAL"]},
        {"op": "add-literals", "enumeration": "$mode", "literals": ["SAFE"]},
        {"op": "create-exchange-item", "as": "msg", "layer": "la", "name": "PositionMsg", "mechanism": "flow"},
        {"op": "add-exchange-item-element", "exchange_item": "$msg", "name": "pos", "type": "$pos"},
        {"op": "assign-exchange-item", "exchange_item": "$msg", "elements": [fe]},
    ]))
    ids = {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}
    ids["fe"], ids["integer"] = fe, integer
    return ids


def test_types_are_visible_downwards(run):
    sa = {i["name"] for i in run("data", "types", "--layer", "sa")["items"]}
    la = run("data", "types", "--layer", "la")["items"]
    assert {"Integer", "Boolean", "String"} <= sa
    assert {i["layer"] for i in la} <= {"oa", "sa", "la"} and any(i["layer"] == "la" for i in la)
    assert all(i["layer"] != "la" for i in run("data", "types", "--layer", "sa")["items"])


def test_class_enum_exchange_item_round_trip(run):
    ids = _build(run)
    cls = run("show", ids["pos"])  # `show` routes data elements to `data show`
    props = {p["name"]: p for p in cls["properties"]}
    assert props["lat"]["type"]["uuid"] == ids["integer"] and props["lat"]["multiplicity"] == "1..1"
    assert props["history"]["multiplicity"] == "0..*" and props["history"]["kind"] == "COMPOSITION"
    assert cls["issues"] == []
    assert run("data", "show", ids["mode"])["literals"] == ["AUTO", "MANUAL", "SAFE"]
    ei = run("show", ids["msg"])
    assert ei["mechanism"] == "FLOW"
    assert ei["elements"][0]["type"]["uuid"] == ids["pos"]
    assert [c["uuid"] for c in ei["carried_by"]] == [ids["fe"]]
    assert ei["issues"] == []
    assert run("check")["ok"]


def test_xml_matches_capella(run, model):
    ids = _build(run)
    tree = _xml(model)
    prop = next(p for p in _by_id(tree, ids["pos"]) if p.get("name") == "history")
    assert prop.get("aggregationKind") == "COMPOSITION"
    assert prop.find("ownedMinCard").get("value") == "0" and prop.find("ownedMaxCard").get("value") == "*"
    assert prop.find("ownedMinCard").get("{http://www.w3.org/2001/XMLSchema-instance}type").endswith(":LiteralNumericValue")
    for lit in _by_id(tree, ids["mode"]):
        assert lit.get("abstractType") == "#" + ids["mode"]
    elem = _by_id(tree, ids["msg"]).find("ownedElements")
    assert (elem.get("direction"), elem.get("composite")) == ("UNSET", "true")
    assert elem.find("ownedMaxCard").get("value") == "1"
    assert f"#{ids['msg']}" in _by_id(tree, ids["fe"]).get("exchangedItems").split()


def test_rules_and_errors(run):
    ids = _build(run)
    sa_cls = run("data", "class", "create", "--layer", "sa", "--name", "S")["created"]["uuid"]
    data, code = run("data", "property", "add", sa_cls, "--name", "p", "--type", ids["pos"], ok=False)
    assert code == 1 and "not visible from sa" in data["error"]
    data, code = run("data", "property", "add", ids["pos"], "--name", "lat", "--type", ids["integer"], ok=False)
    assert code == 1 and "already has" in data["error"]
    data, code = run("data", "property", "add", ids["pos"], "--name", "x", "--type", ids["fe"], ok=False)
    assert code == 1 and "data types" in data["error"]
    data, code = run("data", "property", "add", ids["pos"], "--name", "x", "--type", ids["integer"], "--min", "3", "--max", "2", ok=False)
    assert code == 1
    data, code = run("data", "class", "create", "--parent", "la:root-component", "--name", "X", ok=False)
    assert code == 1 and "data package" in data["error"]
    fn = run("list", "la", "functions")["items"][1]["uuid"]
    data, code = run("data", "assign", ids["msg"], fn, ok=False)
    assert code == 1 and "function ports" in data["error"]


def test_assign_ports_component_exchanges_and_remove(run):
    ids = _build(run)
    fn = next(f["uuid"] for f in run("list", "la", "functions")["items"] if run("show", f["uuid"], "--attr", "outputs")["outputs"])
    port = run("show", fn, "--attr", "outputs")["outputs"][0]["uuid"]
    ce = run("list", "la", "component-exchanges")["items"][0]["uuid"]
    res = run("data", "assign", ids["msg"], port, ce, ids["fe"])
    assert len(res["assigned_to"]) == 2 and res["unchanged"][0]["uuid"] == ids["fe"]
    assert {c["uuid"] for c in run("show", ids["msg"])["carried_by"]} == {ids["fe"], port, ce}
    run("data", "assign", ids["msg"], port, "--remove")
    assert port not in {c["uuid"] for c in run("show", ids["msg"])["carried_by"]}
    assert run("check")["ok"]


def test_delete_exchange_item_detaches_instead_of_deleting_carriers(run):
    ids = _build(run)
    data, code = run("delete", ids["msg"], ok=False)
    assert code == 1 and "exchangedItems" in data["error"]
    res = run("delete", ids["msg"], "--cascade")
    assert [d["type"] for d in res["deleted"]] == ["ExchangeItem"]
    assert res["detached"][0]["from"] == ids["fe"]
    assert run("show", ids["fe"])["uuid"] == ids["fe"]  # the exchange survives
    assert run("check")["ok"]


def test_delete_type_in_use_is_blocked(run):
    ids = _build(run)
    data, code = run("delete", ids["pos"], "--cascade", ok=False)
    assert code == 1 and "ExchangeItemElement" in data["error"]


def test_basic_types_union_collection(run, model):
    res = run("batch", input=json.dumps([
        {"op": "create-type", "as": "b", "layer": "la", "name": "Flag", "kind": "boolean"},
        {"op": "create-type", "as": "n", "layer": "la", "name": "Count", "kind": "integer"},
        {"op": "create-type", "as": "f", "layer": "la", "name": "Ratio", "kind": "float"},
        {"op": "create-type", "as": "s", "layer": "la", "name": "Label", "kind": "string"},
        {"op": "create-union", "as": "u", "layer": "la", "name": "Value"},
        {"op": "add-property", "class": "$u", "name": "count", "type": "$n"},
        {"op": "create-collection", "as": "c", "layer": "la", "name": "Counts", "type": "$n"},
    ]))
    ids = {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}
    tree = _xml(model)
    lits = _by_id(tree, ids["b"]).findall("ownedLiterals")  # Capella writes True/False
    assert [(e.get("name"), e.get("value"), e.get("abstractType")) for e in lits] == [
        ("True", "true", "#" + ids["b"]), ("False", None, "#" + ids["b"])]
    assert _by_id(tree, ids["n"]).get("kind") is None  # INTEGER is the default, not written
    assert (_by_id(tree, ids["f"]).get("kind"), _by_id(tree, ids["f"]).get("discrete")) == ("FLOAT", "false")
    assert [p["name"] for p in run("show", ids["u"])["properties"]] == ["count"]
    col = run("show", ids["c"])
    assert col["item_type"]["uuid"] == ids["n"] and col["multiplicity"] == "0..*" and col["issues"] == []
    data, code = run("batch", input=json.dumps([{"op": "create-type", "layer": "la", "name": "X", "kind": "blob"}]), ok=False)
    assert code == 1 and "boolean, integer" in data["error"]
    pa_type = run("data", "type", "--layer", "pa", "--name", "PaInt", "--kind", "integer")["created"]["uuid"]
    data, code = run("data", "collection", "--layer", "la", "--name", "Bad", "--type", pa_type, ok=False)
    assert code == 1 and "layer" in data["error"]
    assert run("check")["ok"]


def test_generalize(run, model):
    ids = _build(run)
    sub = run("data", "class", "create", "--layer", "la", "--name", "GpsPosition")["created"]["uuid"]
    run("data", "generalize", sub, ids["pos"])
    assert run("data", "generalize", sub, ids["pos"])["unchanged"]
    assert [x["uuid"] for x in run("show", sub)["specializes"]] == [ids["pos"]]
    assert [x["uuid"] for x in run("show", ids["pos"])["specialized_by"]] == [sub]
    g = _by_id(_xml(model), sub).find("ownedGeneralizations")
    assert (g.get("super"), g.get("sub")) == ("#" + ids["pos"], "#" + sub)  # as Capella writes it
    data, code = run("data", "generalize", ids["pos"], sub, ok=False)
    assert code == 1 and "cycle" in data["error"]
    data, code = run("data", "generalize", sub, ids["mode"], ok=False)
    assert code == 1 and "same kind" in data["error"]
    run("data", "generalize", sub, ids["pos"], "--remove")
    assert run("show", sub).get("specializes", []) == []
    base = run("data", "class", "create", "--layer", "sa", "--name", "Base")["created"]["uuid"]
    run("data", "generalize", sub, base)  # a class of a layer above is visible
    run("delete", base, "--cascade")  # the generalization goes with it
    assert run("show", sub).get("specializes", []) == []
    assert run("check")["ok"]
