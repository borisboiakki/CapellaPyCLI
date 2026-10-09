import json

HOGWARTS = "0d2edb8f-fa34-4e73-89ec-fb9a63001440"


def created(run, *args):
    return run(*args)["created"]["uuid"]


def test_info_and_list(run):
    info = run("info")
    assert info["capella_version"] == "7.0.0"
    assert info["layers"]["la"]["counts"]["functions"] > 0
    assert "functions" in run("list", "la")["kinds"]
    res = run("list", "la", "components", "--name", "hogwarts")
    assert [i["uuid"] for i in res["items"]] == [HOGWARTS]


def test_unknown_kind_is_json_error(run):
    data, code = run("list", "la", "nope", ok=False)
    assert code == 1 and "available" in data["error"]


def test_search_hides_parts(run):
    items = run("search", "Hogwarts", "--exact")["items"]
    assert [i["type"] for i in items] == ["LogicalComponent"]
    parts = run("search", "Hogwarts", "--type", "Part")["items"]
    assert parts and all(i["type"] == "Part" for i in parts)


def test_create_exchange_allocate_and_reload(run):
    a = created(run, "create", "function", "--parent", "la:root-function", "--name", "A")
    b = created(run, "create", "function", "--parent", "la:root-function", "--name", "B")
    ex = run("create", "function-exchange", "--source", a, "--target", b, "--name", "data")
    assert len(ex["ports"]) == 2
    c = created(run, "create", "component", "--parent", "la:root-component", "--name", "C")
    run("allocate", a, c)

    shown = run("show", b)  # fresh process-equivalent: model reloaded from disk
    assert shown["incoming_exchanges"][0]["from"]["uuid"] == a
    assert run("show", a)["allocated_to"]["uuid"] == c
    assert run("check")["ok"]


def test_allocate_requires_move(run):
    f = created(run, "create", "function", "--parent", "la:root-function", "--name", "F")
    c1 = created(run, "create", "component", "--parent", "la:root-component", "--name", "C1")
    c2 = created(run, "create", "component", "--parent", "la:root-component", "--name", "C2")
    run("allocate", f, c1)
    data, code = run("allocate", f, c2, ok=False)
    assert code == 1 and "--move" in data["error"]
    res = run("allocate", f, c2, "--move")
    assert res["removed_from"][0]["uuid"] == c1
    assert run("show", f)["allocated_to"]["uuid"] == c2


def test_oa_uses_operational_types(run):
    a = run("create", "function", "--parent", "oa:root-activity", "--name", "Plan")["created"]
    assert a["type"] == "OperationalActivity"
    b = created(run, "create", "function", "--parent", "oa:root-activity", "--name", "Go")
    ex = run("create", "function-exchange", "--source", a["uuid"], "--target", b, "--name", "x")
    assert ex["ports"] == []
    e1 = created(run, "create", "component", "--parent", "oa:root-entity", "--name", "E1")
    e2 = created(run, "create", "component", "--parent", "oa:root-entity", "--name", "E2")
    cm = run("create", "component-exchange", "--source", e1, "--target", e2, "--name", "talk")
    assert cm["created"]["type"] == "CommunicationMean"
    assert run("check")["ok"]


def test_pa_component_nature(run):
    pc = created(run, "create", "component", "--parent", "pa:root-component", "--name", "ECU", "--nature", "node")
    assert run("show", pc, "--attr", "nature")["nature"] == "NODE"


def test_cross_layer_rules(run):
    sf = created(run, "create", "function", "--parent", "sa:root-function", "--name", "S")
    lf = created(run, "create", "function", "--parent", "la:root-function", "--name", "L")
    data, code = run("create", "function-exchange", "--source", sf, "--target", lf, "--name", "x", ok=False)
    assert code == 1 and "one layer" in data["error"]
    run("realize", lf, sf)
    assert run("show", lf)["realized_functions"][0]["uuid"] == sf


def test_delete_refuses_then_cascades(run):
    steps = [
        {"op": "create-function", "as": "a", "parent": "la:root-function", "name": "A"},
        {"op": "create-function", "as": "b", "parent": "la:root-function", "name": "B"},
        {"op": "create-function-exchange", "as": "x", "source": "$a", "target": "$b", "name": "d"},
        {"op": "create-component", "as": "c1", "parent": "la:root-component", "name": "C1"},
        {"op": "create-component", "as": "c2", "parent": "la:root-component", "name": "C2"},
        {"op": "create-component-exchange", "as": "cx", "source": "$c1", "target": "$c2", "name": "bus"},
        {"op": "allocate", "element": "$b", "to": "$c2"},
        {"op": "allocate", "element": "$x", "to": "$cx"},
    ]
    res = run("batch", input=json.dumps(steps))
    ids = {s["as"]: s["created"]["uuid"] for s in res["steps"] if "as" in s}

    data, code = run("delete", ids["b"], ok=False)
    assert code == 1 and "--cascade" in data["error"]

    dry = run("--dry-run", "delete", ids["b"], "--cascade")
    assert dry["saved"] is False
    assert run("show", ids["b"])["uuid"] == ids["b"]  # still there

    res = run("delete", ids["b"], "--cascade")
    types = {d["type"] for d in res["deleted"]}
    assert {"LogicalFunction", "FunctionalExchange", "ComponentFunctionalAllocation"} <= types
    assert "FunctionOutputPort" in types  # orphaned port on A is cleaned up
    assert run("check")["ok"]

    res = run("delete", ids["c1"], "--cascade")
    assert "Part" in {d["type"] for d in res["deleted"]}
    assert run("check")["ok"]


def test_batch_is_atomic(run, model):
    before = (model / "Model Test 7.0.capella").read_bytes()
    steps = [
        {"op": "create-function", "as": "a", "parent": "la:root-function", "name": "A"},
        {"op": "allocate", "element": "$a", "to": "$missing"},
    ]
    data, code = run("batch", input=json.dumps(steps), ok=False)
    assert code == 1 and "step 1" in data["error"]
    assert (model / "Model Test 7.0.capella").read_bytes() == before


def test_set_attributes(run):
    data = run("set", HOGWARTS, "name=Hogwarts School", "is_human=false", "description=<p>x</p>")
    assert data["values"]["name"] == "Hogwarts School"
    data, code = run("set", HOGWARTS, "components=foo", ok=False)
    assert code == 1 and "reference" in data["error"]


def test_refuses_to_delete_roots(run):
    data, code = run("delete", "la:root-function", ok=False)
    assert code == 1 and "root" in data["error"]


def test_diagram_render(run, tmp_path):
    d = run("diagrams", "list", "--type", "LAB")["items"][0]
    out = tmp_path / "d.svg"
    run("diagrams", "render", d["uuid"], "-o", str(out))
    assert out.read_text().lstrip().startswith("<")


def test_validate(run):
    assert "failures" in run("validate", "--layer", "la")


# ------------------------------------------------------------ functional chains

SA_CHAIN = "dfc4341d-253a-4ae9-8a30-63a9d9faca39"


def _nav_model(run, layer="la"):
    root = "oa:root-activity" if layer == "oa" else f"{layer}:root-function"
    steps = [
        {"op": "create-function", "as": n, "parent": root, "name": n}
        for n in ("acquire", "compute", "display", "log")
    ] + [
        {"op": "create-function-exchange", "as": x, "source": f"${s}", "target": f"${t}", "name": x}
        for x, s, t in (("pos", "acquire", "compute"), ("route", "compute", "display"), ("trace", "compute", "log"))
    ]
    res = run("batch", input=json.dumps(steps))
    return {s["as"]: s["created"]["uuid"] for s in res["steps"]}


def test_chain_read_existing(run):
    items = run("chain", "list", "sa")["items"]
    assert [i["uuid"] for i in items] == [SA_CHAIN] and items[0]["issues"] == 0
    shown = run("show", SA_CHAIN)  # `show` on a chain renders it as a chain
    assert [s["exchange"]["name"] for s in shown["steps"]] == ["Test fex"]
    assert shown["entry"][0]["name"] == "Sysexfunc"


def test_chain_create_along_path_and_branch(run):
    ids = _nav_model(run)
    res = run(
        "chain", "create", "--layer", "la", "--name", "Navigate",
        "--path", ids["acquire"], "--path", ids["compute"], "--path", ids["display"],
        "--add", ids["trace"],
    )
    ch = res["created"]["uuid"]
    shown = run("chain", "show", ch)
    assert [s["exchange"]["name"] for s in shown["steps"]] == ["pos", "route", "trace"]
    assert {e["name"] for e in shown["exit"]} == {"display", "log"}
    assert shown["issues"] == []
    assert run("show", ids["compute"])["chains"][0]["uuid"] == ch
    assert run("chain", "list", "la", "--involving", ids["trace"])["count"] == 1
    assert run("check")["ok"]


def test_chain_path_needs_exchange(run, model):
    ids = _nav_model(run)
    before = (model / "Model Test 7.0.capella").read_bytes()
    data, code = run(
        "chain", "create", "--layer", "la", "--name", "x",
        "--path", ids["acquire"], "--path", ids["display"], ok=False,
    )
    assert code == 1 and "No functional exchange" in data["error"]
    assert (model / "Model Test 7.0.capella").read_bytes() == before


def test_chain_remove_and_issues(run):
    ids = _nav_model(run)
    ch = run(
        "chain", "create", "--layer", "la", "--name", "N",
        "--path", ids["acquire"], "--path", ids["compute"], "--path", ids["display"],
    )["created"]["uuid"]
    run("chain", "remove", ch, ids["route"])
    issues = run("chain", "show", ch)["issues"]
    assert len(issues) == 1 and "display" in issues[0]
    res = run("chain", "remove", ch, ids["display"])
    assert [r["type"] for r in res["removed"]] == ["FunctionalChainInvolvementFunction"]
    assert run("chain", "show", ch)["issues"] == []
    assert run("show", ids["display"])["uuid"] == ids["display"]  # function kept
    assert run("check")["ok"]


def test_chain_involve_realize_and_delete(run):
    ids = _nav_model(run, "sa")
    ch = run(
        "chain", "create", "--layer", "sa", "--name", "N",
        "--path", ids["acquire"], "--path", ids["compute"],
    )["created"]["uuid"]
    cap = run("list", "sa", "capabilities")["items"][0]["uuid"]
    run("chain", "involve", ch, cap)
    assert run("chain", "involve", ch, cap)["unchanged"]

    lf = _nav_model(run, "la")
    lch = run(
        "chain", "create", "--layer", "la", "--name", "LN",
        "--path", lf["acquire"], "--path", lf["compute"],
    )["created"]["uuid"]
    run("realize", lch, ch)
    assert run("chain", "show", ch)["realizing_chains"][0]["uuid"] == lch

    # Deleting a function used by a chain is guarded, and cascades cleanly.
    data, code = run("delete", ids["compute"], ok=False)
    assert code == 1 and "FunctionalChainInvolvement" in data["error"]
    run("delete", ids["compute"], "--cascade")
    assert run("check")["ok"]
    res = run("delete", ch, "--cascade")
    assert "FunctionalChainAbstractCapabilityInvolvement" in {d["type"] for d in res["deleted"]}
    assert run("check")["ok"]


def test_operational_process(run):
    ids = _nav_model(run, "oa")
    res = run("batch", input=json.dumps([
        {"op": "create-chain", "as": "p", "layer": "oa", "name": "Trip",
         "path": [f"{ids['acquire']}", f"{ids['compute']}"]},
        {"op": "chain-add", "chain": "$p", "elements": [ids["route"]]},
    ]))
    p = res["steps"][0]["created"]
    assert p["type"] == "OperationalProcess"
    assert [s["exchange"]["name"] for s in run("chain", "show", p["uuid"])["steps"]] == ["pos", "route"]
    assert run("check")["ok"]
