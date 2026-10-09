import json
import pathlib
import shutil

import pytest
from click.testing import CliRunner

from capcli.cli import cli

DATA = pathlib.Path(__file__).parent / "data" / "model"


@pytest.fixture
def model(tmp_path):
    dst = tmp_path / "model"
    shutil.copytree(DATA, dst)
    return dst


@pytest.fixture
def run(model):
    runner = CliRunner()

    def _run(*args, input=None, ok=True):
        res = runner.invoke(cli, ["--model", str(model), *args], input=input)
        if ok:
            assert res.exit_code == 0, res.output
        data = json.loads(res.output)
        return (data, res.exit_code) if not ok else data

    return _run
