import json

from lxml import etree


def _xml(model):
    return etree.parse(str(model / "Model Test 7.0.capella"))


def _el(tree, uuid):
    return next(e for e in tree.iter() if isinstance(e.tag, str) and e.get("id") == uuid)


def _ids(v):
    return sorted(t[1:] for t in (v or "").split())


def _build(run):
    fn = run("create", "function", "--parent", "sa:root-function", "--name", "power up")["created"]["uuid"]
    fe = run("list", "sa", "function-exchanges")["items"][0]["uuid"]
    res = run("batch", input=json.dumps([
        {"op": "create-state-machine", "as": "sm", "owner": "sa:root-component"},
        {"op": "add-state", "as": "init", "parent": "$sm", "name": "start", "kind": "initial"},
        {"op": "add-state", "as": "off", "parent": "$sm", "name": "Off"},
        {"op": "add-state", "as": "on", "parent": "$sm", "name": "On"},
        {"op": "add-state", "as": "idle", "parent": "$on", "name": "Idle"},
        {"op": "add-state", "as": "busy", "parent": "$on", "name": "Busy"},
        {"op": "add-transition", "source": "$init", "target": "$off"},
        {"op": "add-transition", "source": "$off", "target": "$on", "triggers": [fe],
         "guard": "power > 10", "trigger_description": "power on"},
        {"op": "add-transition", "source": "$on", "target": "$off", "effects": [fn]},
        {"op": "set-available", "state": "$on", "elements": [fn]},
    ]))
    ids = {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}
    ids.update(fn=fn, fe=fe)
    return ids


def test_read_existing_machines(run):
    oa = run("mode", "list", "oa")["items"]
    weather = next(i for i in oa if i["owner"]["name"] == "Weather")
    shown = run("show", weather["uuid"])  # `show` routes to `mode show`
    assert [s["name"] for s in shown["regions"][0]["states"]] == ["Day", "Night"]
    assert shown["issues"] == []  # mode machines without an initial state are fine
    assert run("check")["ok"]


def test_build_machine_and_read_back(run):
    ids = _build(run)
    sm = run("mode", "show", ids["sm"])
    assert sm["name"] == "System State Machine" and sm["owner"]["name"] == "System"
    top = sm["regions"][0]
    assert [s["name"] for s in top["states"]] == ["start", "Off", "On"]
    on = next(s for s in top["states"] if s["name"] == "On")
    assert [s["name"] for s in on["regions"][0]["states"]] == ["Idle", "Busy"]
    assert on["available"][0]["uuid"] == ids["fn"]
    guarded = next(t for t in top["transitions"] if t.get("guard"))
    assert guarded["guard"] == "power > 10" and guarded["trigger_description"] == "power on"
    assert guarded["triggers"][0]["uuid"] == ids["fe"]
    assert any(t.get("effects", [{}])[0].get("uuid") == ids["fn"] for t in top["transitions"])
    assert "region 'region' has several states but no initial state" in sm["issues"]
    state = run("show", ids["on"])
    assert [s["name"] for s in state["sub_states"]] == ["Idle", "Busy"]
    assert state["incoming"][0]["from"]["uuid"] == ids["off"]
    assert run("check")["ok"]


def test_xml_caches_and_guard_match_capella(run, model):
    ids = _build(run)
    tree = _xml(model)
    on = _el(tree, ids["on"])
    region = on.getparent()
    assert _ids(region.get("involvedStates")) == sorted([ids["init"], ids["off"], ids["on"]])
    assert _ids(on.get("referencedStates")) == sorted([ids["idle"], ids["busy"]])
    assert [r.get("name") for r in _el(tree, ids["off"]) if r.tag == "ownedRegions"] == ["region"]
    tr = next(e for e in region if e.tag == "ownedTransitions" and e.get("guard"))
    spec = _el(tree, tr.get("guard")[1:]).find("ownedSpecification")
    assert spec.get("{http://www.w3.org/2001/XMLSchema-instance}type").endswith(":OpaqueExpression")
    assert (spec.find("bodies").text, spec.find("languages").text) == ("power > 10", "capella:linkedText")


def test_rules(run):
    ids = _build(run)
    for args, needle in [
        (("mode", "add", ids["sm"], "--name", "M", "--kind", "mode"), "does not mix"),
        (("mode", "add", ids["sm"], "--name", "i2", "--kind", "initial"), "already has an initial"),
        (("mode", "transition", ids["off"], ids["init"]), "initial state cannot"),
        (("mode", "machine", "create", ids["fn"]), "components, actors or entities"),
    ]:
        data, code = run(*args, ok=False)
        assert code == 1 and needle in data["error"], data
    la_fn = run("list", "la", "functions")["items"][1]["uuid"]
    data, code = run("mode", "available", ids["on"], la_fn, ok=False)
    assert code == 1 and "one layer" in data["error"]
    other = run("mode", "list", "la")["items"][0]["uuid"]
    other_state = run("mode", "show", other)["regions"][0]["states"][0]["uuid"]
    data, code = run("mode", "transition", ids["off"], other_state, ok=False)
    assert code == 1
    assert run("mode", "available", ids["on"], ids["fn"])["unchanged"]


def test_available_remove(run):
    ids = _build(run)
    run("mode", "available", ids["on"], ids["fn"], "--remove")
    assert run("mode", "show", ids["on"])["available"] == []
    assert run("check")["ok"]


def test_delete_state_cascades_transitions_and_detaches(run):
    ids = _build(run)
    data, code = run("delete", ids["on"], ok=False)
    assert code == 1 and "StateTransition" in data["error"]
    res = run("delete", ids["on"], "--cascade")
    assert [d["type"] for d in res["deleted"]].count("StateTransition") == 2
    assert {d["attr"] for d in res["detached"]} >= {"availableInStates", "involvedStates"}
    assert run("show", ids["fn"])["uuid"] == ids["fn"]  # the function is kept
    assert run("check")["ok"]  # caches rebuilt, nothing dangling


def test_delete_effect_function_keeps_transition(run):
    ids = _build(run)
    res = run("delete", ids["fn"], "--cascade")
    assert "StateTransition" not in {d["type"] for d in res["deleted"]}
    # availableInStates lives on the deleted function itself; only the effect is detached.
    assert {d["attr"] for d in res["detached"]} == {"effect"}
    top = run("mode", "show", ids["sm"])["regions"][0]
    assert len(top["transitions"]) == 3 and not any("effects" in t for t in top["transitions"])
    assert run("check")["ok"]


def test_check_detects_and_fixes_stale_caches(run, model):
    ids = _build(run)
    path = model / "Model Test 7.0.capella"
    tree = _xml(model)
    del _el(tree, ids["on"]).getparent().attrib["involvedStates"]
    tree.write(str(path), xml_declaration=True, encoding="UTF-8")
    data, code = run("check", ok=False)
    assert code == 2 and data["state_caches"]
    assert run("check", "--fix")["saved"]
    assert run("check")["ok"]
