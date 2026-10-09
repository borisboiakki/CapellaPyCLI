import json

from lxml import etree

SA_CAP = "9390b7d5-598a-42db-bef8-23677e45ba06"  # "Capability"
SYSTEM = "230c4621-7e0a-4d0a-9db2-d4ba5e97b3df"
STAY_ALIVE = "83d1334f-6180-46c4-a80d-6839341df688"  # OA capability


def _ids(res):
    return {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}


def _capella_xml(model):
    return etree.parse(str(model / "Model Test 7.0.capella"))


def test_read_existing_capability(run):
    shown = run("show", STAY_ALIVE)  # `show` on a capability renders it fully
    assert shown["type"] == "OperationalCapability"
    assert shown["included_by"][0]["name"] == "Escape predators"
    assert shown["extended_by"][0]["name"] == "Eat food"
    assert shown["realized_by"][0]["uuid"] == SA_CAP
    assert any(a["name"] == "Functional Human Being" for a in shown["involves"]["actors"])
    assert run("capability", "list", "oa")["count"] == 6


def test_create_per_layer_types(run):
    expected = {"oa": "OperationalCapability", "sa": "Capability", "la": "CapabilityRealization", "pa": "CapabilityRealization"}
    for layer, metaclass in expected.items():
        res = run("capability", "create", "--layer", layer, "--name", f"cap {layer}")
        assert res["created"]["type"] == metaclass
    assert run("check")["ok"]


def test_involve_uninvolve_and_issues(run):
    res = run("batch", input=json.dumps([
        {"op": "create-capability", "as": "c", "layer": "sa", "name": "Navigate"},
        {"op": "create-function", "as": "f", "parent": "sa:root-function", "name": "compute route"},
        {"op": "capability-involve", "capability": "$c", "elements": ["$f", SYSTEM]},
    ]))
    ids = _ids(res)
    shown = run("capability", "show", ids["c"])
    assert [f["uuid"] for f in shown["involves"]["functions"]] == [ids["f"]]
    assert [c["uuid"] for c in shown["involves"]["components"]] == [SYSTEM]
    assert "does not realize any oa capability" in shown["issues"]
    assert run("capability", "involve", ids["c"], ids["f"])["unchanged"]
    assert run("capability", "list", "sa", "--involving", ids["f"])["count"] == 1

    # A logical component cannot be involved in a system capability.
    lc = run("list", "la", "components")["items"][1]["uuid"]
    data, code = run("capability", "involve", ids["c"], lc, ok=False)
    assert code == 1 and "one layer" in data["error"]

    run("capability", "uninvolve", ids["c"], ids["f"])
    assert run("capability", "show", ids["c"])["involves"]["functions"] == []
    assert run("show", ids["f"])["uuid"] == ids["f"]  # function kept
    assert run("check")["ok"]


def test_function_allocated_outside_capability_is_reported(run):
    res = run("batch", input=json.dumps([
        {"op": "create-capability", "as": "c", "layer": "la", "name": "C"},
        {"op": "create-function", "as": "f", "parent": "la:root-function", "name": "f"},
        {"op": "create-component", "as": "k", "parent": "la:root-component", "name": "K"},
        {"op": "allocate", "element": "$f", "to": "$k"},
        {"op": "capability-involve", "capability": "$c", "elements": ["$f"]},
    ]))
    issues = run("capability", "show", _ids(res)["c"])["issues"]
    assert any("allocated to 'K'" in i for i in issues)


def test_pa_component_involvement_reads_back(run):
    cap = run("capability", "create", "--layer", "pa", "--name", "P")["created"]["uuid"]
    pc = run("list", "pa", "components")["items"][1]["uuid"]
    run("capability", "involve", cap, pc)
    assert [c["uuid"] for c in run("capability", "show", cap)["involves"]["components"]] == [pc]


def test_realization_chain_writes_source_element(run, model):
    res = run("batch", input=json.dumps([
        {"op": "create-capability", "as": "s", "layer": "sa", "name": "S"},
        {"op": "realize", "element": "$s", "realized": STAY_ALIVE},
        {"op": "create-capability", "as": "l", "layer": "la", "name": "L"},
        {"op": "realize", "element": "$l", "realized": "$s"},
        {"op": "create-capability", "as": "p", "layer": "pa", "name": "P"},
        {"op": "realize", "element": "$p", "realized": "$l"},
    ]))
    ids = _ids(res)
    assert run("capability", "show", ids["l"])["realizes"][0]["uuid"] == ids["s"]
    assert run("capability", "show", ids["l"])["realized_by"][0]["uuid"] == ids["p"]
    assert ids["s"] in {c["uuid"] for c in run("show", STAY_ALIVE)["realized_by"]}

    # Capella always writes both ends of a realization; so must we.
    links = [
        el for el in _capella_xml(model).iter()
        if isinstance(el.tag, str) and el.get("targetElement") in {f"#{ids['l']}", f"#{ids['s']}", f"#{STAY_ALIVE}"}
        and el.getparent().get("id") in ids.values()
    ]
    assert len(links) == 3
    assert all(el.get("sourceElement") == "#" + el.getparent().get("id") for el in links)

    # Cross-layer rule: an LA capability cannot realize an OA one directly.
    data, code = run("realize", ids["l"], STAY_ALIVE, ok=False)
    assert code == 1

    run("unrealize", ids["p"], ids["l"])
    assert run("capability", "show", ids["l"])["realized_by"] == []
    assert run("check")["ok"]


def test_function_realization_also_gets_source_element(run, model):
    f = run("create", "function", "--parent", "la:root-function", "--name", "f")["created"]["uuid"]
    sf = run("list", "sa", "functions")["items"][1]["uuid"]
    run("realize", f, sf)
    link = [el for el in _capella_xml(model).iter() if isinstance(el.tag, str) and el.getparent() is not None and el.getparent().get("id") == f and el.get("targetElement")]
    assert link and link[0].get("sourceElement") == f"#{f}"


def test_check_reports_and_fixes_missing_source(run, model):
    f = run("create", "function", "--parent", "la:root-function", "--name", "f")["created"]["uuid"]
    run("realize", f, run("list", "sa", "functions")["items"][1]["uuid"])
    path = model / "Model Test 7.0.capella"
    path.write_text(path.read_text().replace(f' sourceElement="#{f}"', "").replace(f'sourceElement="#{f}" ', ""))
    data, code = run("check", ok=False)
    assert code == 2 and len(data["incomplete"]) == 1
    fixed = run("check", "--fix")
    assert fixed["fixed"] == 1 and fixed["saved"]
    assert run("check")["ok"]


def test_relations_include_extend_generalize(run):
    a = run("capability", "create", "--layer", "sa", "--name", "A")["created"]["uuid"]
    b = run("capability", "create", "--layer", "sa", "--name", "B")["created"]["uuid"]
    run("capability", "include", a, b)
    run("capability", "extend", a, SA_CAP)
    run("capability", "generalize", b, SA_CAP)
    shown = run("capability", "show", a)
    assert [c["uuid"] for c in shown["includes"]] == [b]
    assert [c["uuid"] for c in shown["extends"]] == [SA_CAP]
    assert [c["uuid"] for c in run("capability", "show", b)["included_by"]] == [a]
    assert [c["uuid"] for c in run("capability", "show", b)["specializes"]] == [SA_CAP]
    assert run("capability", "include", a, b)["unchanged"]
    run("capability", "include", a, b, "--remove")
    assert run("capability", "show", a)["includes"] == []
    data, code = run("capability", "include", a, a, ok=False)
    assert code == 1
    assert run("check")["ok"]


def test_missions(run):
    actor = run("list", "sa", "actors")["items"][0]["uuid"]
    res = run("batch", input=json.dumps([
        {"op": "create-capability", "as": "c", "layer": "sa", "name": "C"},
        {"op": "create-mission", "as": "m", "name": "M"},
        {"op": "mission-exploit", "mission": "$m", "capability": "$c"},
        {"op": "mission-involve", "mission": "$m", "elements": [actor]},
    ]))
    ids = _ids(res)
    shown = run("show", ids["m"])
    assert [c["uuid"] for c in shown["exploits"]] == [ids["c"]]
    assert [a["uuid"] for a in shown["involves"]] == [actor]
    assert run("capability", "show", ids["c"])["exploited_by_missions"][0]["uuid"] == ids["m"]
    run("mission", "exploit", ids["m"], ids["c"], "--remove")
    assert run("mission", "show", ids["m"])["exploits"] == []
    assert run("check")["ok"]


def test_delete_capability_is_guarded_and_cascades(run):
    res = run("batch", input=json.dumps([
        {"op": "create-capability", "as": "s", "layer": "sa", "name": "S"},
        {"op": "create-capability", "as": "l", "layer": "la", "name": "L"},
        {"op": "realize", "element": "$l", "realized": "$s"},
        {"op": "create-capability", "as": "o", "layer": "sa", "name": "O"},
        {"op": "capability-include", "capability": "$o", "other": "$s"},
        {"op": "create-mission", "as": "m", "name": "M"},
        {"op": "mission-exploit", "mission": "$m", "capability": "$s"},
    ]))
    ids = _ids(res)
    data, code = run("delete", ids["s"], ok=False)
    assert code == 1 and "--cascade" in data["error"]
    deleted = {d["type"] for d in run("delete", ids["s"], "--cascade")["deleted"]}
    assert {"Capability", "AbstractCapabilityRealization", "AbstractCapabilityInclude", "CapabilityExploitation"} <= deleted
    assert run("capability", "show", ids["l"])["realizes"] == []
    assert run("check")["ok"]


def test_deleting_involved_function_cascades_involvement(run):
    res = run("batch", input=json.dumps([
        {"op": "create-capability", "as": "c", "layer": "la", "name": "C"},
        {"op": "create-function", "as": "f", "parent": "la:root-function", "name": "f"},
        {"op": "capability-involve", "capability": "$c", "elements": ["$f"]},
    ]))
    ids = _ids(res)
    data, code = run("delete", ids["f"], ok=False)
    assert code == 1 and "Involvement" in data["error"]
    run("delete", ids["f"], "--cascade")
    assert run("capability", "show", ids["c"])["involves"]["functions"] == []
    assert run("check")["ok"]


def test_pre_and_postconditions(run, model):
    run("capability", "condition", SA_CAP, "--pre", "power is on", "--post", "target tracked")
    shown = run("show", SA_CAP)
    assert (shown["precondition_text"], shown["postcondition_text"]) == ("power is on", "target tracked")
    run("capability", "condition", SA_CAP, "--pre", "power is stable")  # replaces, no orphan
    tree = _capella_xml(model)
    cap = next(e for e in tree.iter() if isinstance(e.tag, str) and e.get("id") == SA_CAP)
    constraints = cap.findall("ownedConstraints")
    assert len(constraints) == 2  # pre + post, the old pre was removed
    pre = next(c for c in constraints if "#" + c.get("id") == cap.get("preCondition"))
    spec = pre.find("ownedSpecification")
    assert spec.get("{http://www.w3.org/2001/XMLSchema-instance}type").endswith(":OpaqueExpression")
    assert [spec.findtext("bodies"), spec.findtext("languages")] == ["power is stable", "capella:linkedText"]
    run("capability", "condition", SA_CAP, "--clear-post")
    shown = run("show", SA_CAP)
    assert shown["precondition_text"] == "power is stable" and "postcondition_text" not in shown
    chain = run("chain", "list", "sa")["items"][0]["uuid"]
    run("chain", "condition", chain, "--post", "image displayed")
    assert run("chain", "show", chain)["postcondition_text"] == "image displayed"
    fn = run("list", "sa", "functions")["items"][1]["uuid"]
    data, code = run("capability", "condition", fn, "--pre", "x", ok=False)
    assert code == 1 and "capabilities and functional chains" in data["error"]
    data, code = run("capability", "condition", SA_CAP, ok=False)
    assert code == 1 and "--pre" in data["error"]
    assert run("check")["ok"]
