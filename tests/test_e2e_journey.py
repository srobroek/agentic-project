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


def test_only_the_absent_tools_own_manifests_are_excused():
    """With cargo absent, e2e failed rust-cli on "expected files missing: Cargo.toml"
    before the journey could report the skip, so a missing toolchain read as a defect.
    """
    warnings = [{"step": "lang-rust", "message": "native_init: WARNING cargo is not on PATH"}]
    assert e2e.absent_tools(warnings) == {"cargo"}
    assert "Cargo.toml" in e2e.NATIVE_OUTPUT["cargo"]
    assert "go.mod" not in e2e.NATIVE_OUTPUT["cargo"]


# `just setup` and `just check` do real network work -- toolchain downloads, package
# installs, and (for check) cloning the four remote hook repos on a fresh prek cache.
# A transient failure there should retry rather than fail the whole gate; a genuine
# code defect must repeat identically and never pass just because it was retried.


def flaky_command(counter: object, succeed_on_attempt: int, failure_line: str) -> str:
    """A shell command that fails with `failure_line` until the Nth run, via a counter
    file, so each retry is a real new subprocess rather than a mocked return value."""
    return (
        f"c=$(cat {counter} 2>/dev/null || echo 0); c=$((c + 1)); echo $c > {counter}; "
        f"if [ $c -lt {succeed_on_attempt} ]; then echo '{failure_line}' >&2; exit 1; fi"
    )


def test_a_transient_network_failure_retries_and_the_success_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(e2e.time, "sleep", lambda _seconds: None)
    counter = tmp_path / "attempts"
    command = flaky_command(counter, succeed_on_attempt=2, failure_line="Connection timed out")
    retried: list[str] = []
    e2e.journey_step(tmp_path, "setup", command, retries=2, retried=retried)
    assert counter.read_text().strip() == "2"
    assert retried == ["setup (succeeded on attempt 2)"]


def test_retries_exhaust_and_the_failure_names_the_attempt_count(tmp_path, monkeypatch):
    monkeypatch.setattr(e2e.time, "sleep", lambda _seconds: None)
    counter = tmp_path / "attempts"
    command = flaky_command(counter, succeed_on_attempt=99, failure_line="network is unreachable")
    with pytest.raises(e2e.Failure) as caught:
        e2e.journey_step(tmp_path, "check", command, retries=2)
    assert counter.read_text().strip() == "3"  # 1 original attempt + 2 retries, then it gave up
    assert "after 3 attempts, still a transient symptom" in str(caught.value)


def test_a_deterministic_failure_is_never_retried_even_when_retries_are_allowed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(e2e.time, "sleep", lambda _seconds: None)
    counter = tmp_path / "attempts"
    command = flaky_command(counter, succeed_on_attempt=99, failure_line="ruff: E501 line too long")
    with pytest.raises(e2e.Failure) as caught:
        e2e.journey_step(tmp_path, "check", command, retries=2)
    assert counter.read_text().strip() == "1"  # one attempt only: no network symptom to retry on
    assert "attempts" not in str(caught.value)


@pytest.mark.parametrize(
    "line",
    [
        "curl: (6) Could not resolve host: proxy.golang.org",
        "dial tcp: lookup registry.npmjs.org: Temporary failure in name resolution",
        "fatal: unable to access 'https://github.com/...': Connection timed out",
        "npm error network 429 Too Many Requests",
        "Error: ETIMEDOUT",
    ],
)
def test_network_transient_matches_real_transport_symptoms(line):
    assert e2e.NETWORK_TRANSIENT.search(line)


def test_network_transient_does_not_match_a_tool_verdict():
    assert not e2e.NETWORK_TRANSIENT.search("cargo fmt --check failed: 3 files need formatting")
    assert not e2e.NETWORK_TRANSIENT.search("AssertionError: expected 2, got 3")
