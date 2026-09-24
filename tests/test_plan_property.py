"""`plan` reports exactly what the `apply` after it does, in every disposition.

tools/e2e.py runs this for all twelve stacks. Here it runs for one, so the unit suite
fails on a lying plan without the full end-to-end run.
"""

from __future__ import annotations

from pathlib import Path

import plan_property
import pytest
import yaml

from project_setup.cli import load_preset

PRESETS = Path(__file__).resolve().parents[1] / "presets"


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict[str, list[str]]:
    work = tmp_path_factory.mktemp("plan-property")
    data_file = work / "answers.yml"
    data = {**load_preset("minimal", PRESETS)[0], "PROJECT_NAME": "my-app", "DESCRIPTION": "x"}
    data_file.write_text(yaml.safe_dump(data))
    return plan_property.check_stack(work / "minimal", data_file)


@pytest.mark.parametrize("disposition", [name for name, _ in plan_property.DISPOSITIONS])
def test_plan_matches_apply(results, disposition):
    assert results[disposition] == []


def test_the_property_catches_a_lie(tmp_path, monkeypatch):
    """A property that cannot fail proves nothing: make plan omit one file."""
    data_file = tmp_path / "answers.yml"
    data = {**load_preset("minimal", PRESETS)[0], "PROJECT_NAME": "my-app", "DESCRIPTION": "x"}
    data_file.write_text(yaml.safe_dump(data))
    dest = tmp_path / "dest"
    dest.mkdir()
    # The CLI runs in a subprocess, so the lie is injected there through sitecustomize.
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import sys\n"
        "if sys.argv[1:2] == ['plan']:\n"
        "    from project_setup import runner\n"
        "    real = runner.classify\n"
        "    def lying(before, after):\n"
        "        changes = real(before, after)\n"
        "        changes.create = changes.create[1:]\n"
        "        return changes\n"
        "    runner.classify = lying\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(shim))
    found = plan_property.mismatches(dest, data_file)
    assert any(line.startswith("plan create") for line in found), found
