"""Keep the agent instructions (AGENTS.md and the skill) in sync with the CLI."""

import json
import pathlib
import re

import click
import pytest

from capcli.cli import cli
from capcli.ops import OPS

ROOT = pathlib.Path(__file__).parent.parent
AGENTS = ROOT / "templates" / "AGENTS.md"
SKILL = ROOT / "templates" / "skills" / "capella-model" / "SKILL.md"
DOCS = [AGENTS, SKILL]


def _commands(group, prefix=""):
    for name, cmd in group.commands.items():
        if isinstance(cmd, click.Group):
            yield from _commands(cmd, f"{prefix}{name} ")
        else:
            yield f"{prefix}{name}"


def test_skill_frontmatter_follows_agent_skills_rules():
    text = SKILL.read_text()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    fields = dict(
        line.split(":", 1) for line in m.group(1).splitlines() if line and not line.startswith(" ")
    )
    name = fields["name"].strip()
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    assert name == SKILL.parent.name, "skill name must match its folder"
    assert 1 <= len(fields["description"].strip()) <= 1024


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_command_is_documented(doc):
    text = doc.read_text()
    missing = [c for c in _commands(cli) if f"capcli {c}" not in text and f"capcli --dry-run {c}" not in text]
    assert not missing, f"{doc.name} does not mention: {missing}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_batch_op_is_documented(doc):
    text = doc.read_text()
    missing = [op for op in OPS if f"`{op}`" not in text]
    assert not missing, f"{doc.name} does not list batch ops: {missing}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_batch_example_runs(doc, run):
    m = re.search(r"capcli batch <<'EOF'\n(.*?)\nEOF", doc.read_text(), re.S)
    assert m, f"{doc.name} has no batch example"
    res = run("batch", input=m.group(1))
    assert res["saved"] and len(res["steps"]) == len(json.loads(m.group(1)))
    assert run("check")["ok"]
