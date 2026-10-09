"""delete / check / batch / CLI contract: regressions found in the code review."""

import json
import os
import pathlib
import subprocess
import sys

LA_PRECONDITION = "e9d60f7e-fddd-4200-8c03-ba3bf73c7fcf"  # owned by an LA CapabilityRealization
GUARD = "1e54ce22-b4ad-4638-a1e9-1154827ec496"  # a transition's guard constraint
SPAWN = "a0159943-264f-4a97-a245-565fb6bf9db4"  # linked from a guard's text
TEST_FEX = "1a414995-f4cd-488c-8152-486e459fb9de"
REQ_NAMED = "TestReq1"


def _ids(res):
    return {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}


def _owner_of(run, uuid):
    return run("show", uuid)["parent"]["uuid"]


# ------------------------------------------------------------------ delete


def test_deleting_a_constraint_never_deletes_its_owner(run):
    cap = _owner_of(run, LA_PRECONDITION)
    res = run("delete", LA_PRECONDITION, "--cascade")
    assert [d["uuid"] for d in res["deleted"]] == [LA_PRECONDITION]
    assert [d["attr"] for d in res["detached"]] == ["preCondition"]
    assert run("show", cap)["uuid"] == cap  # the LA capability is still there
    transition = _owner_of(run, GUARD)
    run("delete", GUARD, "--cascade")
    assert run("show", transition)["uuid"] == transition
    assert run("check")["ok"]


def test_delete_reports_links_left_in_text(run):
    res = run("delete", SPAWN, "--cascade")
    assert SPAWN in {t["links_to"] for t in res["text_links"]} and "text_warning" in res


def test_delete_keeps_ports_that_carry_interfaces(run):
    res = run("batch", input=json.dumps([
        {"op": "create-interface", "as": "i", "layer": "la", "name": "I"},
        {"op": "create-component", "as": "a", "parent": "la:root-component", "name": "A"},
        {"op": "create-component", "as": "b", "parent": "la:root-component", "name": "B"},
        {"op": "create-component-exchange", "as": "cx", "source": "$a", "target": "$b", "name": "x"},
    ]))
    ids = _ids(res)
    port = run("show", ids["cx"], "--attr", "source")["source"]["uuid"]
    run("interface", "provide", port, ids["i"])
    deleted = {d["uuid"] for d in run("delete", ids["cx"])["deleted"]}
    assert port not in deleted
    assert [p["uuid"] for p in run("show", ids["i"])["provided_by_ports"]] == [port]
    assert run("check")["ok"]


def test_delete_fresh_component_and_package_without_cascade(run):
    comp = run("create", "component", "--parent", "la:root-component", "--name", "Fresh")["created"]["uuid"]
    res = run("delete", comp)  # its own Part goes with it
    assert {d["type"] for d in res["deleted"]} == {"LogicalComponent", "Part"}
    pkg = run("package", "create", "--parent", "la:functions", "--name", "Tmp")["created"]["uuid"]
    assert [d["uuid"] for d in run("delete", pkg)["deleted"]] == [pkg]
    data, code = run("delete", "la:functions", ok=False)
    assert code == 1 and "root package" in data["error"]
    assert run("check")["ok"]


def test_delete_warns_about_split_chains_and_counts_diagram_refs(run):
    ids = _ids(run("batch", input=json.dumps([
        {"op": "create-function", "as": "a", "parent": "la:root-function", "name": "A"},
        {"op": "create-function", "as": "b", "parent": "la:root-function", "name": "B"},
        {"op": "create-function", "as": "c", "parent": "la:root-function", "name": "C"},
        {"op": "create-function-exchange", "source": "$a", "target": "$b", "name": "ab"},
        {"op": "create-function-exchange", "source": "$b", "target": "$c", "name": "bc"},
        {"op": "create-chain", "as": "ch", "layer": "la", "name": "ABC", "path": ["$a", "$b", "$c"]},
    ])))
    res = run("delete", ids["b"], "--cascade")
    assert [c["uuid"] for c in res["affected_chains"]] == [ids["ch"]] and "chain_warning" in res
    res = run("delete", TEST_FEX, "--cascade")
    assert res["warning"].startswith(f"{len(res['deleted'])} deleted")  # removed ports included


def test_check_ignores_hash_text_in_requirement_attributes(run):
    req = next(r["uuid"] for r in run("search", REQ_NAMED)["items"])
    run("set", req, "name=#42")
    assert run("check")["ok"]


# ------------------------------------------------------------------- batch


def test_batch_dollar_text_escapes_and_errors(run):
    res = run("batch", input=json.dumps([
        {"op": "create-function", "as": "f", "parent": "la:root-function", "name": "$5 budget"},
        {"op": "set", "element": "$f", "values": {"description": "$$x"}},
    ]))
    f = _ids(res)["f"]
    shown = run("show", f)
    assert shown["name"] == "$5 budget" and shown["description"] == "$x"
    data, code = run("batch", input=json.dumps([{"op": "set", "element": "$nope", "values": {}}]), ok=False)
    assert code == 1 and "unknown alias" in data["error"]
    data, code = run("batch", input=json.dumps([1]), ok=False)
    assert code == 1 and "expected an object" in data["error"]
    data, code = run("batch", input=json.dumps([{"op": "move", "element": f, "where": "x"}]), ok=False)
    assert code == 1 and "move takes: element, to" in data["error"]


def test_set_accepts_json_values(run):
    entity = run("list", "oa", "entities")["items"][0]["uuid"]
    run("batch", input=json.dumps([{"op": "set", "element": entity, "values": {"is_human": True}}]))
    assert run("show", entity, "--attr", "is_human")["is_human"] is True
    data, code = run("batch", input=json.dumps([{"op": "set", "element": entity, "values": {"is_human": "maybe"}}]), ok=False)
    assert code == 1 and "true or false" in data["error"]


def test_uppercase_layers(run):
    assert run("show", "OA:root-function")["type"] == "OperationalActivity"
    assert run("show", "LA:Structure")["type"] == "LogicalComponentPkg"
    res = run("batch", input=json.dumps([{"op": "create-capability", "layer": "LA", "name": "Up"}]))
    assert res["steps"][0]["created"]["type"] == "CapabilityRealization"
    data, code = run("batch", input=json.dumps([{"op": "create-chain", "layer": "la", "name": "c", "kind": "bogus"}]), ok=False)
    assert code == 1 and "SIMPLE, COMPOSITE, FRAGMENT" in data["error"]


# --------------------------------------------------------------------- CLI


def test_noop_write_does_not_save(run, model):
    fn = run("create", "function", "--parent", "la:root-function", "--name", "f")["created"]["uuid"]
    files = sorted(model.glob("*.capella")) + sorted(model.glob("*.aird"))
    before = [p.stat().st_mtime_ns for p in files]
    res = run("reorder", fn, "--last")
    assert res["unchanged"] and res["saved"] is False
    assert [p.stat().st_mtime_ns for p in files] == before


def test_main_keeps_the_json_contract(model):
    """Through the real entry point: usage errors are JSON with exit 1, output is UTF-8."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}

    def capcli(*args):
        p = subprocess.run([sys.executable, "-c", "from capcli.cli import main; main()", "-m", str(model), *args],
                           capture_output=True, env=env, cwd=pathlib.Path(model).parent)
        return json.loads(p.stdout.decode("utf-8")), p.returncode

    data, code = capcli("--bogus")
    assert code == 1 and "No such option" in data["error"]
    data, code = capcli("list", "la", "functions", "--limit", "-1")
    assert code == 1 and "--limit" in data["error"]
    data, code = capcli("create", "function", "--parent", "la:root-function", "--name", "Route → λ")
    assert code == 0 and data["created"]["name"] == "Route → λ"
