"""What an interrupted apply leaves behind, whether the user can tell, and whether
re-running recovers.

Measured before any of this existed, by SIGKILLing apply at every layer and generator
boundary of minimal, ts-service, py-lib and go-service (60 points): re-running the
same apply always reproduced the uninterrupted scaffold byte for byte. Two things did
not hold. Nothing in the checkout said the scaffold was incomplete -- a re-apply over
an existing scaffold even leaves the previous answers file in place -- and a kill
inside a native init left a manifest every later run skipped as "present". These pin
both, and the recovery itself.
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from project_setup.catalog import load_catalog
from project_setup.cli import checkout_problems, load_preset
from project_setup.runner import INCOMPLETE_FILE, SCAN_SKIP, scaffold

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "templates"
PRESETS = REPO / "presets"
IDENTITY = {"PROJECT_NAME": "my-app", "DESCRIPTION": "A thing"}

# Runs the real CLI, and SIGKILLs itself when the Nth layer or generator is about to
# start: no handler runs, which is the worst case a Ctrl-C or a closed terminal gives.
KILLER = r"""
import os, signal, subprocess, sys
kind, n = sys.argv[1], int(sys.argv[2])
sys.argv = ["project-setup"] + sys.argv[3:]
import copier
from project_setup import cli, runner
seen = {"layer": 0, "generator": 0}
def count(step):
    seen[step] += 1
    if step == kind and seen[step] == n:
        os.kill(os.getpid(), signal.SIGKILL)
real_copy, real_run = copier.run_copy, subprocess.run
def run_copy(*a, **k):
    count("layer")
    return real_copy(*a, **k)
def run(cmd, *a, **k):
    if isinstance(cmd, list) and len(cmd) > 1 and "/scripts/" in str(cmd[1]):
        count("generator")
    return real_run(cmd, *a, **k)
copier.run_copy, runner.subprocess.run = run_copy, run
raise SystemExit(cli.main())
"""


def fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SCAN_SKIP]
        for name in files:
            path = Path(current, name)
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def apply(dest: Path, data_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "project_setup.cli", "apply", "--data-file", str(data_file),
         "--dest", str(dest)],
        capture_output=True, text=True, cwd=REPO, check=False,
    )  # fmt: skip


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    work = tmp_path_factory.mktemp("interrupt")
    data_file = work / "answers.yml"
    data_file.write_text(yaml.safe_dump({**load_preset("minimal", PRESETS)[0], **IDENTITY}))
    dest = work / "reference"
    assert apply(dest, data_file).returncode == 0
    return work, data_file, fingerprint(dest)


@pytest.mark.parametrize(
    ("kind", "n"), [("layer", 1), ("layer", 4), ("generator", 1), ("generator", 6)]
)
def test_a_killed_apply_is_visible_and_the_next_one_finishes_it(reference, kind, n):
    work, data_file, want = reference
    dest = work / f"{kind}-{n}"
    dest.mkdir()
    killed = subprocess.run(
        [sys.executable, "-c", KILLER, kind, str(n), "apply", "--data-file", str(data_file),
         "--dest", str(dest)],
        capture_output=True, text=True, cwd=REPO, check=False,
    )  # fmt: skip
    assert killed.returncode == -signal.SIGKILL

    # The user can tell: the checkout says so, and so does every command that reads it.
    assert (dest / INCOMPLETE_FILE).is_file()
    catalog = load_catalog(TEMPLATES)
    data = yaml.safe_load(data_file.read_text())
    assert "INTERRUPTED_APPLY" in [p.code for p in checkout_problems(catalog, dest, data)]

    # And re-running the same apply is the whole recovery.
    assert apply(dest, data_file).returncode == 0
    assert not (dest / INCOMPLETE_FILE).exists()
    assert fingerprint(dest) == want


def test_a_failed_step_leaves_the_scaffold_marked_incomplete(tmp_path: Path):
    """A step marked FAIL used to leave a checkout indistinguishable from a finished
    one once any earlier apply had written the answers file."""
    catalog = load_catalog(TEMPLATES)
    data = {**load_preset("minimal", PRESETS)[0], **IDENTITY}
    broken = {**data, "ADRS": '[{"title": "No decision recorded"}]'}

    assert not scaffold(catalog, tmp_path, broken, keep_dirs=frozenset()).ok
    assert (tmp_path / INCOMPLETE_FILE).is_file()

    assert scaffold(catalog, tmp_path, data, keep_dirs=frozenset()).ok
    assert not (tmp_path / INCOMPLETE_FILE).exists()
    assert "INTERRUPTED_APPLY" not in [p.code for p in checkout_problems(catalog, tmp_path, data)]
