"""Arcadia structure rules that capellambse does not enforce."""

import pathlib
import re

from helpers import named


def test_sa_system_is_a_black_box(run):
    data, code = run("create", "component", "--parent", "sa:root-component", "--name", "Flight Control System", ok=False)
    assert code == 1 and "black box" in data["error"]
    data, code = run("create", "component", "--parent", "sa:structure", "--name", "Second System", ok=False)
    assert code == 1 and "black box" in data["error"]


def test_actors_live_in_the_structure_package(run):
    for layer in ("sa", "la", "pa"):
        data, code = run("create", "component", "--parent", f"{layer}:root-component", "--name", "Operator", "--actor", ok=False)
        assert code == 1 and f"{layer}:structure" in data["error"]
        res = run("create", "component", "--parent", f"{layer}:structure", "--name", "Operator", "--actor")
        assert res["parent"]["type"].endswith("ComponentPkg")
    assert run("check")["ok"]


def test_logical_decomposition_still_allowed(run):
    res = run("create", "component", "--parent", "la:root-component", "--name", "Flight Control")
    assert res["created"]["type"] == "LogicalComponent"
    assert run("check")["ok"]


def test_is_actor_cannot_be_toggled(run):
    actor = named(run("list", "sa", "actors")["items"], "Kevin Spacey")
    data, code = run("set", actor, "is_actor=false", ok=False)
    assert code == 1 and "is_actor" in data["error"]


def test_check_reports_existing_violations(run, model):
    # Recreate the mistakes from a real project by editing the XML the way
    # plain capellambse would: a sub-system and an actor inside the SA System.
    path = model / "Model Test 7.0.capella"
    text = path.read_text()
    system = re.search(r'<ownedSystemComponents xsi:type="org.polarsys.capella.core.data.ctx:SystemComponent"\s+id="230c4621-7e0a-4d0a-9db2-d4ba5e97b3df" name="System"[^>]*?(/?)>', text)
    assert system
    inner = (
        '<ownedSystemComponents xsi:type="org.polarsys.capella.core.data.ctx:SystemComponent" '
        'id="11111111-1111-4111-8111-111111111111" name="Flight Control System"/>'
        '<ownedSystemComponents xsi:type="org.polarsys.capella.core.data.ctx:SystemComponent" '
        'id="22222222-2222-4222-8222-222222222222" name="Operator" actor="true"/>'
    )
    tag = system.group(0)
    replacement = tag[:-2] + ">" + inner + "</ownedSystemComponents>" if system.group(1) else tag + inner
    path.write_text(text.replace(tag, replacement, 1))

    data, code = run("check", ok=False)
    assert code == 2
    problems = {v["name"]: v["problem"] for v in data["structure"]}
    assert "black box" in problems["Flight Control System"]
    assert "Structure package" in problems["Operator"]


def test_docs_never_suggest_actors_inside_the_system():
    root = pathlib.Path(__file__).parent.parent
    for doc in (root / "templates" / "AGENTS.md", root / "templates" / "skills" / "capella-model" / "SKILL.md"):
        for line in doc.read_text().splitlines():
            if "--actor" in line:
                assert "root-component" not in line, f"{doc.name}: {line}"
