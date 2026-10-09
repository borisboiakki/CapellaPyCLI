import json

from lxml import etree

PROGRESS = ["DRAFT", "TO_BE_REVIEWED", "TO_BE_DISCUSSED", "REWORK_NECESSARY", "UNDER_REWORK", "REVIEWED_OK"]
EAT_FOOD = "3b83b4ba-671a-4de8-9c07-a5c6b1d3c422"  # OA capability with status TO_BE_DISCUSSED


def _fn(run, i=1):
    return run("list", "la", "functions")["items"][i]["uuid"]


def test_values_and_existing_statuses(run):
    assert run("status", "values")["values"] == PROGRESS
    listed = run("status", "list")
    assert listed["count"] == 3
    assert EAT_FOOD in {e["uuid"] for e in listed["by_status"]["TO_BE_DISCUSSED"]}
    assert run("show", EAT_FOOD)["status"] == "TO_BE_DISCUSSED"


def test_set_many_clear_and_idempotence(run, model):
    f1, f2 = _fn(run, 1), _fn(run, 2)
    res = run("status", "set", "draft", f1, f2)  # case-insensitive
    assert [c["to"] for c in res["changed"]] == ["DRAFT", "DRAFT"]
    assert run("status", "set", "DRAFT", f1)["unchanged"][0]["uuid"] == f1
    assert run("show", f1)["status"] == "DRAFT"
    assert run("status", "list", "DRAFT", "--layer", "la")["count"] == 2

    # Written the way Capella writes it: a reference to the literal.
    draft = [el for el in etree.parse(str(model / "Model Test 7.0.capella")).iter()
             if isinstance(el.tag, str) and el.get("name") == "DRAFT" and el.getparent().get("name") == "ProgressStatus"][0]
    el = [e for e in etree.parse(str(model / "Model Test 7.0.capella")).iter() if isinstance(e.tag, str) and e.get("id") == f1][0]
    assert el.get("status") == "#" + draft.get("id")

    run("status", "set", "NOT_SET", f1)
    assert "status" not in run("show", f1)
    assert run("check")["ok"]


def test_rejects_unknown_and_pvmt_literals(run):
    f = _fn(run)
    for bad in ("bogus", "Muggle"):  # "Muggle" is a PVMT enumeration literal in the test model
        data, code = run("status", "set", bad, f, ok=False)
        assert code == 1 and "REVIEWED_OK" in data["error"]
    data, code = run("set", f, "status=Muggle", ok=False)
    assert code == 1


def test_set_command_and_batch(run):
    f = _fn(run)
    assert run("set", f, "status=reviewed_ok")["values"]["status"] == "REVIEWED_OK"
    assert run("set", f, "progress_status=TO_BE_REVIEWED")["values"]["progress_status"] == "TO_BE_REVIEWED"
    res = run("batch", input=json.dumps([
        {"op": "create-capability", "as": "c", "layer": "la", "name": "C"},
        {"op": "set-status", "value": "UNDER_REWORK", "elements": ["$c", f]},
    ]))
    assert len(res["steps"][1]["changed"]) == 2
    assert run("status", "list", "UNDER_REWORK")["count"] == 2
    assert run("check")["ok"]


def test_status_on_other_element_kinds(run):
    comp = run("list", "la", "components")["items"][1]["uuid"]
    ex = run("list", "la", "function-exchanges")["items"][0]["uuid"]
    run("status", "set", "TO_BE_REVIEWED", comp, ex)
    assert run("show", comp)["status"] == "TO_BE_REVIEWED"
    assert run("show", ex)["status"] == "TO_BE_REVIEWED"


def test_status_in_dedicated_show_views(run):
    chain = run("chain", "list", "sa")["items"][0]["uuid"]
    mission = run("mission", "create", "--name", "M")["created"]["uuid"]
    run("status", "set", "DRAFT", chain, mission)
    assert run("show", chain)["status"] == "DRAFT"
    assert run("show", mission)["status"] == "DRAFT"
    assert run("capability", "show", EAT_FOOD)["status"] == "TO_BE_DISCUSSED"
