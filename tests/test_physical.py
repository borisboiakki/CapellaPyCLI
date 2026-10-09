import json

from helpers import by_id, capella_xml


def _build(run):
    res = run("batch", input=json.dumps([
        {"op": "create-component", "as": "ecu", "parent": "pa:root-component", "name": "ECU", "nature": "node"},
        {"op": "create-component", "as": "gw", "parent": "pa:root-component", "name": "Gateway", "nature": "node"},
        {"op": "create-component", "as": "cam", "parent": "pa:root-component", "name": "Camera", "nature": "node"},
        {"op": "create-component", "as": "nav", "parent": "pa:root-component", "name": "NavSW", "nature": "behavior"},
        {"op": "create-component", "as": "vid", "parent": "pa:root-component", "name": "VideoSW", "nature": "behavior"},
        {"op": "deploy", "element": "$nav", "host": "$ecu"},
        {"op": "deploy", "element": "$vid", "host": "$cam"},
        {"op": "create-component-exchange", "as": "ce", "source": "$vid", "target": "$nav", "name": "video"},
        {"op": "create-physical-link", "as": "l1", "source": "$cam", "target": "$gw", "name": "CAN-A"},
        {"op": "create-physical-link", "as": "l2", "source": "$gw", "target": "$ecu", "name": "CAN-B"},
        {"op": "create-physical-path", "as": "p", "name": "cam-to-ecu", "links": ["$l1", "$l2"]},
        {"op": "allocate", "element": "$ce", "to": "$p"},
    ]))
    return {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}


def test_read_existing_physical_architecture(run):
    paths = run("pa", "list", "paths")["items"]
    path = run("show", next(p["uuid"] for p in paths if p["name"] == "Internet via cable"))
    assert [h["name"] for h in path["hops"]] == ["ISP Network", "Optical cable", "Router", "Ethernet cable", "Computer"]
    assert path["issues"] == []
    behaviors = {b["name"]: [h["name"] for h in b["deployed_on"]] for b in run("pa", "list", "behaviors")["items"]}
    assert behaviors["Camera Driver SWC"] == ["Card 1 OS"]  # software on software
    assert run("check")["ok"]


def test_build_and_read_back(run):
    ids = _build(run)
    path = run("show", ids["p"])
    assert [h["name"] for h in path["hops"]] == ["Camera", "CAN-A", "Gateway", "CAN-B", "ECU"]
    assert [c["uuid"] for c in path["allocated_component_exchanges"]] == [ids["ce"]]
    assert path["issues"] == []  # video runs Camera -> ECU, the path's ends
    nav = run("pa", "show", ids["nav"])
    assert nav["nature"] == "BEHAVIOR" and [h["uuid"] for h in nav["deployed_on"]] == [ids["ecu"]]
    ecu = run("pa", "show", ids["ecu"])
    assert [d["uuid"] for d in ecu["deployed"]] == [ids["nav"]]
    assert [p["links"][0]["uuid"] for p in ecu["physical_ports"]] == [ids["l2"]]
    link = run("show", ids["l1"])
    assert {e["component"]["uuid"] for e in link["ends"]} == {ids["cam"], ids["gw"]}
    assert run("check")["ok"]


def test_xml_matches_capella(run, model):
    ids = _build(run)
    tree = capella_xml(model)
    ecu_part = next(e for e in tree.iter() if isinstance(e.tag, str) and e.get("abstractType") == "#" + ids["ecu"])
    dl = ecu_part.find("ownedDeploymentLinks")
    assert dl.get("{http://www.w3.org/2001/XMLSchema-instance}type").endswith(":PartDeploymentLink")
    assert dl.get("location") == "#" + ecu_part.get("id")
    invs = [c for c in by_id(tree, ids["p"]) if c.tag == "ownedPhysicalPathInvolvements"]
    assert len(invs) == 5
    assert [i.get("nextInvolvements") for i in invs] == ["#" + j.get("id") for j in invs[1:]] + [None]
    alloc = by_id(tree, ids["p"]).find("ownedComponentExchangeAllocations")
    assert (alloc.get("sourceElement"), alloc.get("targetElement")) == ("#" + ids["p"], "#" + ids["ce"])


def test_allocation_issues_and_port_allocation(run):
    ids = _build(run)
    run("allocate", ids["ce"], ids["l1"])  # CAN-A only joins Camera and Gateway
    assert any("not deployed on the nodes" in i for i in run("show", ids["l1"])["issues"])
    comp_port = run("show", ids["ce"], "--attr", "source")["source"]["uuid"]
    phys_port = run("show", ids["l1"], "--attr", "ends")["ends"][0]["uuid"]
    assert run("allocate", comp_port, phys_port)["allocated"] == "component port->physical port"
    assert [p["uuid"] for p in run("show", phys_port)["allocated_component_ports"]] == [comp_port]
    run("unallocate", comp_port, phys_port)
    assert run("show", phys_port)["allocated_component_ports"] == []
    assert run("check")["ok"]


def test_rules(run):
    ids = _build(run)
    for args, needle in [
        (("pa", "port", ids["nav"], "--name", "x"), "node components"),
        (("pa", "link", "--source", ids["nav"], "--target", ids["ecu"], "--name", "x"), "node components"),
        (("pa", "link", "--source", ids["ecu"], "--target", ids["ecu"], "--name", "x"), "two different nodes"),
        (("pa", "deploy", ids["ecu"], ids["nav"]), "only be deployed on another node"),
    ]:
        data, code = run(*args, ok=False)
        assert code == 1 and needle in data["error"], data
    twin = run("pa", "link", "--source", ids["cam"], "--target", ids["gw"], "--name", "CAN-A2")["created"]["uuid"]
    data, code = run("pa", "path", "--name", "x", "--link", ids["l1"], "--link", ids["l2"], "--link", twin, ok=False)
    assert code == 1 and "consecutive" in data["error"]
    data, code = run("pa", "path", "--name", "x", "--link", ids["l1"], "--link", ids["l1"], ok=False)
    assert code == 1 and "appears twice" in data["error"]
    assert run("pa", "deploy", ids["nav"], ids["ecu"])["unchanged"]


def test_undeploy(run):
    ids = _build(run)
    run("pa", "deploy", ids["nav"], ids["ecu"], "--remove")
    assert "behaviour component is not deployed on any node" in run("pa", "show", ids["nav"])["issues"]
    assert run("check")["ok"]


def test_delete_link_in_path(run):
    ids = _build(run)
    data, code = run("delete", ids["l1"], ok=False)
    assert code == 1 and "PhysicalPath" in data["error"]
    deleted = {d["type"] for d in run("delete", ids["l1"], "--cascade")["deleted"]}
    assert {"PhysicalLink", "PhysicalPath"} <= deleted
    assert run("pa", "list", "paths")["count"] == 4  # only the test model's own paths remain
    assert run("check")["ok"]


def test_delete_deployed_component(run):
    ids = _build(run)
    deleted = {d["type"] for d in run("delete", ids["nav"], "--cascade")["deleted"]}
    assert {"PhysicalComponent", "Part", "PartDeploymentLink"} <= deleted
    assert run("pa", "show", ids["ecu"])["deployed"] == []
    assert run("check")["ok"]


def test_link_categories(run, model):
    ids = _build(run)
    cat = run("pa", "category", "--parent", "pa:root-component", "--name", "CAN buses")["created"]["uuid"]
    run("pa", "categorize", cat, ids["l1"], ids["l2"])
    assert run("pa", "categorize", cat, ids["l1"])["unchanged"]
    assert [c["uuid"] for c in run("pa", "show", ids["l1"])["categories"]] == [cat]
    assert {x["uuid"] for x in run("show", cat)["links"]} == {ids["l1"], ids["l2"]}
    el = by_id(capella_xml(model), cat)
    assert el.tag == "ownedPhysicalLinkCategories" and el.get("links").split() == ["#" + ids["l1"], "#" + ids["l2"]]
    data, code = run("pa", "categorize", cat, ids["ce"], ok=False)
    assert code == 1 and "physical links" in data["error"]
    run("pa", "categorize", cat, ids["l2"], "--remove")
    assert [x["uuid"] for x in run("show", cat)["links"]] == [ids["l1"]]
    res = run("delete", ids["l1"], "--cascade")  # the category is kept, the link detached
    assert [d["attr"] for d in res["detached"]] == ["links"]
    assert run("show", cat)["uuid"] == cat
    assert run("check")["ok"]


def test_ring_and_parallel_paths(run, model):
    ids = _build(run)
    back = run("pa", "link", "--source", ids["ecu"], "--target", ids["cam"], "--name", "CAN-C")["created"]["uuid"]
    ring = run("pa", "path", "--name", "ring", "--link", ids["l1"], "--link", ids["l2"], "--link", back)
    shown = run("show", ring["created"]["uuid"])
    assert [h["name"] for h in shown["hops"]] == ["Camera", "CAN-A", "Gateway", "CAN-B", "ECU", "CAN-C", "Camera"]
    assert shown["issues"] == []
    twin = run("pa", "link", "--source", ids["gw"], "--target", ids["cam"], "--name", "CAN-A2")["created"]["uuid"]
    hops = run("pa", "path", "--name", "there and back", "--link", ids["l1"], "--link", twin)["hops"]
    assert [h["name"] for h in hops] == ["Camera", "CAN-A", "Gateway", "CAN-A2", "Camera"]
    assert run("check")["ok"]


def test_redundant_deployment_and_cycles(run):
    ids = _build(run)
    run("pa", "deploy", ids["vid"], ids["gw"])  # VideoSW also runs on the gateway
    assert run("show", ids["p"])["issues"] == []  # still on Camera, at the path's start
    sw = run("create", "component", "--parent", "pa:root-component", "--name", "OS", "--nature", "behavior")["created"]["uuid"]
    run("pa", "deploy", sw, ids["nav"])
    data, code = run("pa", "deploy", ids["nav"], sw, ok=False)
    assert code == 1 and "cycle" in data["error"]
