import json

import capellambse

from helpers import XSI, capella_xml, created_ids, named


def _build(run):
    ei = run("data", "exchange-item", "create", "--layer", "la", "--name", "NavMsg")["created"]["uuid"]
    ids = created_ids(run("batch", input=json.dumps([
        {"op": "create-interface", "as": "i", "layer": "la", "name": "INav"},
        {"op": "interface-items", "interface": "$i", "elements": [ei]},
        {"op": "create-component", "as": "p", "parent": "la:root-component", "name": "Provider"},
        {"op": "create-component", "as": "u", "parent": "la:root-component", "name": "User"},
        {"op": "create-component-exchange", "as": "cx", "source": "$p", "target": "$u", "name": "nav"},
        {"op": "provide-interface", "element": "$p", "interface": "$i"},
        {"op": "require-interface", "element": "$u", "interface": "$i"},
    ])))
    ids["ei"] = ei
    return ids


def test_read_existing_interfaces(run):
    items = run("interface", "list", "la")["items"]
    snape = next(i for i in items if i["name"].startswith("Prof. S. Snape"))
    shown = run("show", snape["uuid"])  # Capella-written implementations are read
    assert {p["name"] for p in shown["provided_by"]} == {"Whomping Willow", "Prof. S. Snape"}
    assert len(shown["exchange_items"]) == 3
    assert run("check")["ok"]


def test_build_and_read_back(run):
    ids = _build(run)
    shown = run("interface", "show", ids["i"])
    assert [x["uuid"] for x in shown["exchange_items"]] == [ids["ei"]]
    assert [x["uuid"] for x in shown["provided_by"]] == [ids["p"]]
    assert [x["uuid"] for x in shown["required_by"]] == [ids["u"]]
    assert shown["issues"] == []
    assert run("interface", "provide", ids["p"], ids["i"])["unchanged"]
    assert run("check")["ok"]


def test_xml_implementation_and_allocation(run, model):
    ids = _build(run)
    pc = run("create", "component", "--parent", "pa:root-component", "--name", "PImpl", "--nature", "behavior")["created"]["uuid"]
    run("interface", "allocate", pc, ids["i"])
    tree = capella_xml(model)
    impl = next(e for e in tree.iter() if isinstance(e.tag, str) and e.tag == "ownedInterfaceImplementations"
                and e.getparent().get("id") == ids["p"])
    assert impl.get("implementedInterface") == "#" + ids["i"] and impl.get("implementedInterfaces") is None
    assert impl.get(XSI).endswith(":InterfaceImplementation")
    alloc = next(e for e in tree.iter() if isinstance(e.tag, str) and e.tag == "ownedInterfaceAllocations")
    assert alloc.get(XSI).endswith(":InterfaceAllocation")
    assert (alloc.get("targetElement"), alloc.get("sourceElement")) == ("#" + ids["i"], "#" + pc)
    assert [x["uuid"] for x in run("show", ids["i"])["allocated_to"]] == [pc]
    assert run("check")["ok"]


def test_ports_remove_and_rules(run):
    ids = _build(run)
    port = run("show", ids["cx"], "--attr", "source")["source"]["uuid"]
    run("interface", "provide", port, ids["i"])
    assert [x["uuid"] for x in run("show", ids["i"])["provided_by_ports"]] == [port]
    run("interface", "provide", port, ids["i"], "--remove")
    run("interface", "require", ids["u"], ids["i"], "--remove")
    run("interface", "provide", ids["p"], ids["i"], "--remove")
    shown = run("show", ids["i"])
    assert shown["provided_by"] == shown["required_by"] == shown["provided_by_ports"] == []
    sa_actor = named(run("list", "sa", "actors")["items"], "Kevin Spacey")
    data, code = run("interface", "provide", sa_actor, ids["i"], ok=False)
    assert code == 1 and "not visible from sa" in data["error"]
    fn = named(run("list", "la", "functions")["items"], "manage the school")
    data, code = run("interface", "provide", fn, ids["i"], ok=False)
    assert code == 1 and "component ports" in data["error"]
    run("interface", "require", ids["u"], ids["i"])
    assert "interface is required but nothing provides it" in run("show", ids["i"])["issues"]


def test_delete_interface(run):
    ids = _build(run)
    port = run("show", ids["cx"], "--attr", "source")["source"]["uuid"]
    run("interface", "provide", port, ids["i"])
    data, code = run("delete", ids["i"], ok=False)
    assert code == 1
    res = run("delete", ids["i"], "--cascade")
    assert {"InterfaceImplementation", "InterfaceUse", "Interface"} <= {d["type"] for d in res["deleted"]}
    assert [d["attr"] for d in res["detached"]] == ["providedInterfaces"]
    assert run("show", port)["uuid"] == port  # the port is kept
    assert run("check")["ok"]


def test_check_fixes_capellambse_plural_attribute(run, model):
    ids = _build(run)
    m = capellambse.MelodyModel(str(model / "Model Test 7.0.aird"))
    m.by_uuid(ids["u"]).implemented_interfaces.append(m.by_uuid(ids["i"]))  # capellambse's buggy write
    m.save()
    data, code = run("check", ok=False)
    assert code == 2 and len(data["interface_implementations"]) == 1
    assert run("check", "--fix")["saved"]
    assert run("check")["ok"]
    assert ids["u"] in {x["uuid"] for x in run("show", ids["i"])["provided_by"]}


def test_interface_allocation_cycle_is_refused(run):
    ids = created_ids(run("batch", input=json.dumps([
        {"op": "create-interface", "as": "a", "layer": "la", "name": "A"},
        {"op": "create-interface", "as": "b", "layer": "la", "name": "B"},
        {"op": "create-interface", "as": "c", "layer": "la", "name": "C"},
        {"op": "allocate-interface", "element": "$a", "interface": "$b"},
        {"op": "allocate-interface", "element": "$b", "interface": "$c"},
    ])))
    data, code = run("interface", "allocate", ids["c"], ids["a"], ok=False)
    assert code == 1 and "cycle" in data["error"]
    assert run("check")["ok"]
