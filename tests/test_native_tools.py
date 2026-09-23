"""Tests for what a native tool leaves behind, and for the tasks that invoke one.

Every case here is something a native tool wrote that a layer owns, or a script a
layer shipped without wiring. Both are junk in a fresh repository; one was data loss.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "templates"
NATIVE_INIT = REPO / "tools/tasks/native_init.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def native_init():
    return load_module(NATIVE_INIT, "native_init_under_test")


def test_bun_leftovers_are_removed_when_bun_created_them(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"name": "wrong-name"}\n')
    (tmp_path / "CLAUDE.md").write_text("bun's generic agent file\n")
    (tmp_path / ".gitignore").write_text("node_modules\n")
    (tmp_path / "index.ts").write_text('console.log("Hello via Bun!");')

    native_init._tidy_after_bun("my-app", pre_existing=set())

    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".gitignore").exists()
    assert not (tmp_path / "index.ts").exists()
    assert '"name": "my-app"' in (tmp_path / "package.json").read_text()


def test_a_file_the_repository_already_had_is_never_deleted(native_init, tmp_path, monkeypatch):
    """Deleting by filename took a brownfield repo's own CLAUDE.md with it.

    `bun init` only runs when there is no package.json, and a repository can have
    hand-written agent instructions and no package.json at the same time.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"name": "my-app"}\n')
    (tmp_path / "CLAUDE.md").write_text("MY instructions, hand written\n")
    (tmp_path / ".gitignore").write_text("/my-own-ignores\n")

    native_init._tidy_after_bun("my-app", pre_existing={"CLAUDE.md", ".gitignore"})

    assert (tmp_path / "CLAUDE.md").read_text() == "MY instructions, hand written\n"
    assert (tmp_path / ".gitignore").read_text() == "/my-own-ignores\n"


def test_a_steering_symlink_is_left_alone(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"name": "my-app"}\n')
    (tmp_path / "AGENTS.md").write_text("the real one\n")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")

    native_init._tidy_after_bun("my-app", pre_existing=set())

    assert (tmp_path / "CLAUDE.md").is_symlink()


# ----------------------------------------------------------------- aws cdk wiring


def test_the_cdk_layer_wires_the_generator_it_ships():
    """The script was shipped, documented nowhere, and referenced by no task.

    `just aws-cdk-synth` then failed on a directory nothing had created, and
    `just check` failed with it, in every fresh web-app scaffold.
    """
    fragment = (TEMPLATES / "infra-aws-cdk/.just.d/aws-cdk.just.jinja").read_text()
    assert "aws-cdk-init:" in fragment
    assert "scripts/init_aws_cdk.py" in fragment
    assert "--cdk-version @@AWS_CDK_VERSION@@" in fragment


def test_the_cdk_aggregate_degrades_before_the_app_is_generated():
    fragment = (TEMPLATES / "infra-aws-cdk/.just.d/aws-cdk.just.jinja").read_text()
    aggregate = fragment.split("aws-cdk:")[-1]
    assert "aws-cdk-init" in aggregate
    assert "exit 0" in aggregate


def test_cdk_init_names_the_tool_that_is_missing(tmp_path, monkeypatch):
    """A missing binary used to surface as a CalledProcessError traceback."""
    script = load_module(
        TEMPLATES / "infra-aws-cdk/scripts/init_aws_cdk.py", "init_aws_cdk_under_test"
    )
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SystemExit) as raised:
        script.run(["definitely-not-a-real-binary"], cwd=tmp_path, what="the cdk generator")
    assert "definitely-not-a-real-binary" in str(raised.value)
    assert "the cdk generator" in str(raised.value)


def test_cdk_init_removes_the_npmignore_it_does_not_need():
    """`cdk init` writes one because its template is an npm package. This is not one."""
    script = load_module(
        TEMPLATES / "infra-aws-cdk/scripts/init_aws_cdk.py", "init_aws_cdk_leftovers"
    )
    assert ".npmignore" in script.LEFTOVERS


def test_cdk_destination_stays_inside_the_repository(tmp_path):
    script = load_module(TEMPLATES / "infra-aws-cdk/scripts/init_aws_cdk.py", "init_aws_cdk_dest")
    root = tmp_path.resolve()
    with pytest.raises(SystemExit):
        script.destination(root, "../escape")
    with pytest.raises(SystemExit):
        script.destination(root, os.sep + "absolute")
    assert script.destination(root, "infrastructure") == root / "infrastructure"
