"""The journey gate in tools/e2e.py: skip only for a stated reason, fail loudly otherwise.

A skip that passed silently is how a Rust scaffold with no Cargo.toml reported success
last time, so the reason is the scaffolder's own warning, never a guess.
"""

from __future__ import annotations

import subprocess

import e2e
import pytest


def everything_on_path(tool: str) -> str:
    return f"/usr/bin/{tool}"


def test_a_toolchain_apply_reported_absent_skips_the_journey_naming_it():
    warnings = [
        {
            "step": "lang-rust",
            "message": "native_init: WARNING cargo is not on PATH, so Cargo.toml was not "
            "created. Install cargo and re-run apply; nothing else is missing.",
        }
    ]
    reason = e2e.journey_skip_reason(warnings, which=everything_on_path)
    assert reason == "apply reported cargo not on PATH"


def test_missing_mise_skips_the_journey_rather_than_passing_it():
    reason = e2e.journey_skip_reason([], which=lambda tool: None if tool == "mise" else "/x")
    assert reason == "mise not on PATH"


def test_a_warning_about_something_else_does_not_skip_the_journey():
    warnings = [{"step": "gen_caller", "message": "gen_caller: WARNING ci.yml left alone"}]
    assert e2e.journey_skip_reason(warnings, which=everything_on_path) is None


def test_a_failing_journey_step_names_the_step_the_exit_code_and_the_first_failure(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    command = "echo 'fix end of files....Failed'; echo 'error: recipe failed'; exit 3"
    with pytest.raises(e2e.Failure) as caught:
        e2e.journey_step(tmp_path, "check", command)
    message = str(caught.value)
    assert message.startswith("check exited 3")
    assert "first failure: fix end of files....Failed" in message


def test_the_journey_refuses_a_tree_the_first_commit_left_dirty(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "untracked.txt").write_text("left behind\n")
    with pytest.raises(e2e.Failure) as caught:
        e2e.journey_step(tmp_path, "clean", e2e.CLEAN_TREE)
    assert "untracked.txt" in str(caught.value)
