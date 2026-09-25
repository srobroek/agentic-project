"""End-to-end checks for the interview through a real terminal.

The generated question-set tests cover the static contract. These tests exercise the
prompt engine as a user sees it: prompt_toolkit receives terminal input, redraws the
screen, and finally writes the answers file.
"""

from __future__ import annotations

import contextlib
import errno
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
    "CUSTOMISE_DEFAULTS",
    "SPDX_ID",
    "CODEOWNER",
    "SECURITY_CONTACT",
    "DEFAULT_BRANCH",
]
ASKED_BOOLS = {
    "PIN_TOOL_VERSIONS",
    "CUSTOMISE_DEFAULTS",
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

    def wait_for(self, key: str, timeout: float = 5.0) -> None:
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

    def wait_for_any(self, keys: set[str], timeout: float = 5.0) -> str:
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

    def finish(self, timeout: float = 8.0) -> int:
        """Reap the child and drain the terminal, returning its process status."""
        deadline = time.monotonic() + timeout
        while self._process.poll() is None and time.monotonic() < deadline:
            self._read(0.02)
        if self._process.poll() is None:
            self._kill()
        status = self._process.wait()
        # The child can have flushed its final redraw between waitpid and EIO.
        while self._read(0.01):
            pass
        self._status = status
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
        "CUSTOMISE_DEFAULTS",
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
