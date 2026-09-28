"""End-to-end checks for the interview through a real terminal.

The generated question-set tests cover the static contract. These tests exercise the
prompt engine as a user sees it: prompt_toolkit receives terminal input, redraws the
screen, and finally writes the answers file.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest
import yaml

try:
    import fcntl
    import pty
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None
    pty = None


TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
INTERVIEW = TEMPLATES / "_interview" / "copier.yml"
PROMPTS = yaml.safe_load(INTERVIEW.read_text())
PROMPT_BY_KEY = {
    key: str(spec["help"])
    for key, spec in PROMPTS.items()
    if isinstance(spec, dict) and spec.get("help")
}

# This is the documented minimal journey: identity, shape, the two gates, and the
# always-on policy answers. WANT_* entries are implementation details of the layer
# selection and are deliberately not prompts.
MINIMAL_SEQUENCE = [
    "PROJECT_NAME",
    "DESCRIPTION",
    "LAYERS",
    "FORGE_PLATFORM",
    "PIN_TOOL_VERSIONS",
    "CUSTOMIZE_DEFAULTS",
    "SPDX_ID",
    "CODEOWNER",
    "SECURITY_CONTACT",
    "DEFAULT_BRANCH",
]
ASKED_BOOLS = {
    "PIN_TOOL_VERSIONS",
    "CUSTOMIZE_DEFAULTS",
    "GO_VENDOR",
    "PY_SRC_LAYOUT",
    "RUST_LIBRARY",
}
OPTIONAL_LAYERS = [
    "release",
    "worktrunk",
    "lang-go",
    "lang-python",
    "lang-ts",
    "lang-rust",
    "api",
    "i18n",
    "a11y",
    "infra-aws-cdk",
]
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")

# How long a prompt may take to reach the PTY. Sized for a busy machine rather than the
# work, because the work is one redraw. Measured on 14 cores: the slowest single prompt
# is 1.1s idle and 2.6s while load climbs, and the first prompt is always the slowest
# because it waits on interpreter start. The previous 5s looked like 4x headroom and was
# not: six of these tests failed together at load 115, all of them inside a wait rather
# than on anything the interview did. Twenty seconds is ~8x the loaded worst case.
#
# The cost of a larger number is that a genuinely hung interview takes longer to report.
# Nothing here asserts a prompt is absent by letting a wait expire, so that cost is only
# paid on a real failure.
PROMPT_TIMEOUT_SECONDS = 20.0
PTY_AVAILABLE = pty is not None and fcntl is not None and hasattr(pty, "openpty")

if not PTY_AVAILABLE:  # pytest gives users a useful reason on Windows and other hosts.
    pytestmark = pytest.mark.skip(reason="interview PTY tests require POSIX pty support")


class InterviewDriver:
    """Small PTY driver that identifies prompts by their declared help text."""

    def __init__(self, dest: Path, *extra: str, columns: int = 80) -> None:
        self.raw = bytearray()
        self.sequence: list[str] = []
        self._position = 0
        self._status: int | None = None
        self._fd, slave = pty.openpty()
        env = os.environ.copy()
        env.update({"TERM": "xterm-256color", "PYTHONUNBUFFERED": "1"})
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "project_setup.cli",
                "interview",
                "--dest",
                str(dest),
                *extra,
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave)
        fcntl.ioctl(
            self._fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", 24, columns, 0, 0),
        )
        os.set_blocking(self._fd, False)

    @property
    def output(self) -> str:
        """Return the transcript without terminal control sequences."""
        return ANSI.sub("", bytes(self.raw).decode("utf-8", "replace"))

    def _read(self, timeout: float) -> bool:
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return False
        try:
            chunk = os.read(self._fd, 65536)
        except OSError as exc:
            if exc.errno in (errno.EIO, errno.EBADF):
                return False
            raise
        if not chunk:
            return False
        self.raw.extend(chunk)
        return True

    def _new_output(self) -> str:
        """Return output not consumed by a previous prompt match."""
        return self.output[self._position :]

    def _consume(self, offset: int, length: int) -> None:
        self._position += offset + length

    def wait_for(self, key: str, timeout: float = PROMPT_TIMEOUT_SECONDS) -> None:
        """Read until the prompt carrying ``key`` has reached the PTY."""
        token = PROMPT_BY_KEY[key]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            new = self._new_output()
            if (offset := new.find(token)) >= 0:
                self._consume(offset, len(token))
                self.sequence.append(key)
                return
            self._read(min(0.05, max(0.0, deadline - time.monotonic())))
        raise AssertionError(f"did not see {key} ({token!r}) in:\n{self.output[-2000:]}")

    def wait_for_text(self, needle: str, timeout: float = PROMPT_TIMEOUT_SECONDS) -> None:
        """Read until ``needle`` reaches the PTY, for output that is not a prompt.

        A validation message is not a question, so `wait_for` cannot see it, and sending
        the next answer without waiting concatenates both into one: "Not A Valid Name"
        followed immediately by "pty-recovered" arrived as a single answer.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.output:
                return
            self._read(min(0.05, max(0.0, deadline - time.monotonic())))
        raise AssertionError(f"did not see {needle!r} in:\n{self.output[-2000:]}")

    def wait_for_any(self, keys: set[str], timeout: float = PROMPT_TIMEOUT_SECONDS) -> str:
        """Identify the next prompt from a set of possible runtime questions."""
        tokens = {key: PROMPT_BY_KEY[key] for key in keys}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            new = self._new_output()
            matches = [
                (offset, key, token)
                for key, token in tokens.items()
                if (offset := new.find(token)) >= 0
            ]
            if matches:
                offset, key, token = min(matches)
                self._consume(offset, len(token))
                self.sequence.append(key)
                return key
            self._read(min(0.05, max(0.0, deadline - time.monotonic())))
        raise AssertionError(f"did not see one of {sorted(keys)} in:\n{self.output[-2000:]}")

    def send(self, data: bytes) -> None:
        os.write(self._fd, data)

    def read_available(self, seconds: float = 0.1) -> str:
        """Drain output produced immediately after a keypress."""
        before = self.output
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._read(min(0.02, max(0.0, deadline - time.monotonic()))):
                continue
        return self.output[len(before) :]

    def finish(self, timeout: float = 30.0) -> int:
        """Reap the child and drain the terminal, returning its process status.

        Raises on a timeout rather than returning the kill status. A SIGKILLed child
        reports -9, which is indistinguishable from an interview that exited badly, and
        that ambiguity has already cost this project one test: it asserted that a
        rejected answer ENDS the interview and passed, because a prompt correctly
        waiting for a correction looks exactly like a process that had to be killed.
        A harness timeout now says so instead of arriving as a number.

        Thirty seconds because the deadline has to clear the machine being busy, not
        just the work. Measured on 14 cores: teardown takes 1.3 to 1.6s idle and 3.2 to
        6.3s under 2x CPU oversubscription. The previous 8s left 1.7s of headroom
        against that worst case, and the full suite runs enough concurrent subprocesses
        to spend it -- this flaked once in a full run while passing 9 for 9 in isolation.
        """
        deadline = time.monotonic() + timeout
        while self._process.poll() is None and time.monotonic() < deadline:
            self._read(0.02)
        overran = self._process.poll() is None
        if overran:
            self._kill()
        status = self._process.wait()
        # The child can have flushed its final redraw between waitpid and EIO.
        while self._read(0.01):
            pass
        self._status = status
        if overran:
            raise AssertionError(
                f"the interview was still running after {timeout}s, so it was killed. "
                f"This is a harness timeout, not an exit status; the prompt may simply "
                f"have been waiting. Last output:\n{self.output[-1500:]}"
            )
        return status

    def _kill(self) -> None:
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()

    def close(self) -> None:
        if self._status is None:
            self._kill()
            self._status = self._process.wait()
        with contextlib.suppress(OSError):
            os.close(self._fd)

    def __enter__(self) -> InterviewDriver:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _all_layer_args() -> list[str]:
    args = [
        part
        for layer in OPTIONAL_LAYERS
        for part in ("--set", f"WANT_{layer.upper().replace('-', '_')}=true")
    ]
    # Keep identity, selection, the asked bools, and SPDX interactive. Settling
    # the other layer answers keeps this regression comfortably below one minute.
    keep = {
        "PROJECT_NAME",
        "DESCRIPTION",
        "LAYERS",
        "FORGE_PLATFORM",
        "PIN_TOOL_VERSIONS",
        "CUSTOMIZE_DEFAULTS",
        "SPDX_ID",
        *ASKED_BOOLS,
    }
    for key, spec in PROMPTS.items():
        if key in keep or not isinstance(spec, dict) or "default" not in spec:
            continue
        if str(spec.get("when", "")).strip().lower() == "false" or spec.get("type") == "bool":
            continue
        value = str(spec["default"])
        if "@@" not in value:
            args.extend(("--set", f"{key}={value}"))
    return args


def _answer_minimal(driver: InterviewDriver, *, select_release: bool = False) -> None:
    """Answer the minimal prompts, optionally selecting the release layer."""
    for key in MINIMAL_SEQUENCE:
        driver.wait_for(key)
        if key == "PROJECT_NAME":
            driver.send(b"pty-app\r")
        elif key == "DESCRIPTION":
            driver.send(b"A PTY-driven project\r")
        elif key == "LAYERS" and select_release:
            driver.send(b" \r")
        elif key == "LAYERS":
            driver.send(b"\r")
        else:
            driver.send(b"\r")


def test_minimal_interview_asks_the_documented_questions_in_order(tmp_path):
    dest = tmp_path / "minimal"
    with InterviewDriver(dest) as driver:
        _answer_minimal(driver)
        assert driver.finish() == 0
    assert driver.sequence == MINIMAL_SEQUENCE
    assert len(driver.sequence) == 10


def test_bool_y_then_enter_does_not_answer_the_following_question(tmp_path):
    """A select consumes Enter; a confirm used to let it fall into the next prompt."""
    dest = tmp_path / "all-layers"
    runtime = {"PROJECT_NAME", "DESCRIPTION", "LAYERS", "FORGE_PLATFORM", "SPDX_ID", *ASKED_BOOLS}
    with InterviewDriver(dest, *_all_layer_args()) as driver:
        remaining = runtime.copy()
        seen_bools: set[str] = set()
        while remaining:
            key = driver.wait_for_any(remaining)
            remaining.remove(key)
            if key == "PROJECT_NAME":
                driver.send(b"pty-all\r")
            elif key == "DESCRIPTION":
                driver.send(b"Every optional layer\r")
            elif key == "LAYERS":
                driver.send(b"\r")
            elif key == "SPDX_ID":
                driver.send(b"\x1b[B\r")
            elif key in ASKED_BOOLS:
                seen_bools.add(key)
                driver.send(b"y\r")
            else:
                driver.send(b"\r")
        assert seen_bools == ASKED_BOOLS
        assert driver.finish() == 0
    answers = yaml.safe_load((dest / ".project-setup-answers.yml").read_text())
    assert answers["SPDX_ID"] == "MIT"


def test_ctrl_c_mid_interview_stops_without_writing(tmp_path):
    dest = tmp_path / "cancel-c"
    with InterviewDriver(dest) as driver:
        driver.wait_for("PROJECT_NAME")
        driver.send(b"cancel-c\r")
        driver.wait_for("DESCRIPTION")
        driver.send(b"\x03")
        assert driver.finish() == 130
        assert "Traceback" not in driver.output
    assert not dest.exists() or not any(dest.iterdir())


def test_ctrl_d_mid_interview_stops_without_writing(tmp_path):
    dest = tmp_path / "cancel-d"
    with InterviewDriver(dest) as driver:
        driver.wait_for("PROJECT_NAME")
        driver.send(b"cancel-d\r")
        driver.wait_for("DESCRIPTION")
        driver.send(b"\x04")
        assert driver.finish() == 130
        assert "Traceback" not in driver.output
    assert not dest.exists() or not any(dest.iterdir())


def test_interview_answers_file_records_typed_values_without_copier_path(tmp_path):
    dest = tmp_path / "answers"
    with InterviewDriver(dest) as driver:
        _answer_minimal(driver, select_release=True)
        assert driver.finish() == 0
    answers = yaml.safe_load((dest / ".project-setup-answers.yml").read_text())
    assert answers["PROJECT_NAME"] == "pty-app"
    assert answers["DESCRIPTION"] == "A PTY-driven project"
    assert answers["LAYERS"] == ["release"]
    assert "_src_path" not in answers


def test_rust_preset_keeps_layers_multiselect_and_deselects_rust_questions(tmp_path):
    dest = tmp_path / "rust-preset"
    with InterviewDriver(dest, "--preset", "rust-cli") as driver:
        driver.wait_for("PROJECT_NAME")
        driver.send(b"rustless\r")
        driver.wait_for("DESCRIPTION")
        driver.send(b"A Rust-free preset run\r")
        driver.wait_for("LAYERS")
        # The optional choices are stable by the generated catalog order; rust is
        # the sixth choice and starts selected by the preset. Toggling it off must
        # remove its layer-owned questions from this run.
        driver.send(b"\x1b[B" * 5 + b" \r")
        remaining = set(MINIMAL_SEQUENCE) | {"FORGE_HOSTNAME", "SETUP_COMMAND", "DEV_COMMAND"}
        remaining -= {"PROJECT_NAME", "DESCRIPTION", "LAYERS"}
        while remaining:
            key = driver.wait_for_any(remaining)
            remaining.remove(key)
            driver.send(b"\r")
        assert driver.finish() == 0
    assert "RUST_LIBRARY" not in driver.sequence
    assert "RUST_VERSION" not in driver.sequence
    answers = yaml.safe_load((dest / ".project-setup-answers.yml").read_text())
    assert "lang-rust" not in answers["LAYERS"]


def test_a_rejected_answer_re_asks_and_the_correction_is_taken(tmp_path):
    """The rejected value never reaches the file, and the conversation survives a typo.

    An earlier version of this test asserted the opposite -- that a rejection ENDS the
    interview -- and passed, because `finish()` SIGKILLs a process still alive after eight
    seconds and a prompt correctly waiting for a new answer looks exactly like that. The
    assertion was measuring the harness. Copier does attach its validator to the
    questionary prompt for an `input` question, so the prompt comes back.

    questionary keeps the rejected text in the buffer so it can be edited, which is why
    this sends Ctrl-U first: without it the new answer appends to the old one and
    "Not A Valid Name" plus "pty-recovered" arrives as one invalid answer.
    """
    dest = tmp_path / "rejected-then-corrected"
    with InterviewDriver(dest) as driver:
        driver.wait_for("PROJECT_NAME")
        driver.send(b"Not A Valid Name\r")
        driver.wait_for_text("must be lowercase")
        driver.send(b"\x15pty-recovered\r")
        for key in MINIMAL_SEQUENCE[1:]:
            driver.wait_for(key)
            driver.send(b"A recovered project\r" if key == "DESCRIPTION" else b"\r")
        code = driver.finish()

    assert code == 0, f"the interview did not survive a rejected answer:\n{driver.output[-1500:]}"
    assert "Validation error" in driver.output, "the rejection was never shown"
    recorded = yaml.safe_load((dest / ".project-setup-answers.yml").read_text())
    assert recorded["PROJECT_NAME"] == "pty-recovered", "the correction was not taken"
    assert "Not A Valid Name" not in str(recorded), "the rejected value reached the file"


def test_the_prompt_returns_after_a_rejection(tmp_path):
    """The prompt itself has to come back, not just the error. Counting the prompt is the
    evidence: once is a validator that killed the run, twice is one that re-asked."""
    dest = tmp_path / "prompt-returns"
    with InterviewDriver(dest) as driver:
        driver.wait_for("PROJECT_NAME")
        driver.send(b"Not A Valid Name\r")
        driver.wait_for_text("must be lowercase")
        driver.send(b"\x15pty-returns\r")
        for key in MINIMAL_SEQUENCE[1:]:
            driver.wait_for(key)
            driver.send(b"A returning project\r" if key == "DESCRIPTION" else b"\r")
        assert driver.finish() == 0

    asked = driver.output.count(PROMPT_BY_KEY["PROJECT_NAME"])
    assert asked >= 2, f"the name prompt appeared {asked} time(s), so it did not re-ask"


def test_accepting_every_default_produces_an_answer_set_that_applies(tmp_path):
    """The path a hurried user takes: Enter at every prompt. The result has to be a
    complete answer set, not one that fails at apply."""
    dest = tmp_path / "all-defaults"
    with InterviewDriver(dest) as driver:
        for key in MINIMAL_SEQUENCE:
            driver.wait_for(key)
            if key == "PROJECT_NAME":
                driver.send(b"pty-defaults\r")
            elif key == "DESCRIPTION":
                driver.send(b"Everything default\r")
            else:
                driver.send(b"\r")
        assert driver.finish() == 0

    applied = subprocess.run(
        [
            "project-setup",
            "apply",
            "--data-file",
            str(dest / ".project-setup-answers.yml"),
            "--dest",
            str(dest),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert json.loads(applied.stdout)["ok"] is True


def test_a_harness_timeout_is_reported_as_one_not_as_an_exit_status(tmp_path):
    """A killed child reports -9, which reads like an exit code and is not one.

    This is the ambiguity that made an earlier test assert the opposite of the truth and
    pass. The driver has to distinguish "the interview failed" from "the interview was
    still going and I gave up on it", because the second is a statement about the harness.

    Driven by never answering the first prompt, so the process is genuinely still running
    at the deadline, with a deadline short enough to keep the test quick.
    """
    with InterviewDriver(tmp_path / "never-answered") as driver:
        driver.wait_for("PROJECT_NAME")
        with pytest.raises(AssertionError, match=r"harness timeout, not an exit status"):
            driver.finish(timeout=0.5)


def test_an_interview_that_exits_badly_still_reports_its_status(tmp_path):
    """The timeout guard must not swallow a real non-zero exit.

    Ctrl-C makes Copier exit 130 of its own accord, so the process is gone before the
    deadline and the status is the interview's own. If finish() raised here too, the
    two SIGINT tests would be asserting nothing.
    """
    with InterviewDriver(tmp_path / "interrupted") as driver:
        driver.wait_for("PROJECT_NAME")
        driver.send(b"\x03")
        assert driver.finish() == 130
