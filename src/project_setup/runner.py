"""Place layers and run the fragment generators.

Copier is driven in-process rather than by subprocess: one interpreter start for a
whole scaffold instead of one per layer.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import copier

from .catalog import Catalog, selected_layers


@contextlib.contextmanager
def capture_fds() -> Iterator[Path]:
    """Capture everything written to fds 1 and 2, including from subprocesses.

    Copier runs tasks as subprocesses that inherit the real file descriptors, so
    contextlib.redirect_stdout cannot see them. Without this, a failing task reports
    only "returned non-zero exit status 1" and the reason is lost -- which is the
    difference between an agent that can fix the problem and one that cannot.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved_out, saved_err = os.dup(1), os.dup(2)
    tmp = Path(tempfile.mkstemp(prefix="project-setup-", suffix=".log")[1])
    sink = os.open(tmp, os.O_WRONLY)
    try:
        os.dup2(sink, 1)
        os.dup2(sink, 2)
        yield tmp
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        for fd in (sink, saved_out, saved_err):
            os.close(fd)


# The generators rewrite shared destinations from the `.d/` fragments each layer
# dropped. They must run once, after every layer is placed -- not as per-layer
# Copier tasks, which would fire before later layers had contributed anything.
# Order matters: gen_caller reads members.json, gen_steering reads the whole tree.
# Some generators need more than the destination. gen_caller writes an `on: push`
# branch list, so a wrong branch means a workflow that never runs and reports nothing.
GENERATORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scripts/fold_gitignore.py", ()),
    ("scripts/merge_hooks.py", ()),
    ("scripts/gen_justfile.py", ()),
    ("scripts/gen_caller.py", ("--default-branch", "{DEFAULT_BRANCH}")),
    ("scripts/gen_steering.py", ()),
    ("scripts/install_agents_index.py", ()),
)


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


@dataclass
class RunResult:
    dest: Path
    layers: list[str]
    placed: list[StepResult] = field(default_factory=list)
    generated: list[StepResult] = field(default_factory=list)
    pretend: bool = False

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.placed + self.generated)

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.placed + self.generated)


def place_layers(
    catalog: Catalog,
    dest: Path,
    data: dict,
    *,
    pretend: bool = False,
    run_tasks: bool = True,
    quiet: bool = True,
) -> RunResult:
    layers = selected_layers(catalog, data)
    result = RunResult(dest=dest, layers=layers, pretend=pretend)

    for name in layers:
        layer = catalog.layers[name]
        started = time.perf_counter()
        error = ""
        with capture_fds() as log:
            try:
                copier.run_copy(
                    str(layer.path),
                    dest,
                    data=data,
                    defaults=True,
                    overwrite=True,
                    pretend=pretend,
                    quiet=True,
                    # Templates are bundled in this repo, so tasks are ours to trust.
                    unsafe=run_tasks,
                    skip_tasks=not run_tasks,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the caller
                error = f"{type(exc).__name__}: {exc}"
        output = log.read_text().strip()
        log.unlink(missing_ok=True)
        if not quiet and output:
            print("\n".join(f"  {line}" for line in output.splitlines()))
        # The task's own message is the useful part; Copier's TaskError only says
        # that the exit status was non-zero.
        detail = "\n".join(part for part in (output, error) if part)
        result.placed.append(
            StepResult(name, not error, detail=detail, seconds=time.perf_counter() - started)
        )
        if error:
            break
    return result


def prune_empty_dirs(dest: Path) -> list[str]:
    """Remove directories left empty because their contents were excluded.

    Copier creates a directory before deciding that every file inside it is
    excluded, so a forge the project does not use still leaves an empty `.gitlab/`
    behind. Deepest-first so nested shells collapse in one pass.
    """
    removed: list[str] = []
    for path in sorted(
        (p for p in dest.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if ".git" in path.parts or any(path.iterdir()):
            continue
        path.rmdir()
        removed.append(str(path.relative_to(dest)))
    return removed


def run_generators(
    dest: Path, result: RunResult, *, data: dict | None = None, quiet: bool = True
) -> RunResult:
    """Run each generator that the placed layers actually installed."""
    answers = data or {}
    for rel, extra in GENERATORS:
        script = dest / rel
        if not script.is_file():
            continue
        args = [a.format(**answers) if "{" in a else a for a in extra]
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(script), str(dest), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        detail = (proc.stdout + proc.stderr).strip()
        result.generated.append(
            StepResult(
                rel,
                proc.returncode == 0,
                detail=detail,
                seconds=time.perf_counter() - started,
            )
        )
        if not quiet and detail:
            print(f"  {rel}: {detail}")
    return result
