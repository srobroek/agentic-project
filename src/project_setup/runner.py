"""Place layers and run the fragment generators.

Copier is driven in-process rather than by subprocess: one interpreter start for a
whole scaffold instead of one per layer.
"""

from __future__ import annotations

import contextlib
import os
import re
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
    """Capture output from fds 1 and 2 *and* from `sys.stdout`/`sys.stderr`.

    Both halves are needed, for different writers:

    Copier runs tasks as subprocesses that inherit the real file descriptors, so
    `contextlib.redirect_stdout` cannot see them. Without the fd-level dup a failing
    task reports only "returned non-zero exit status 1" and its actual message is
    lost -- the difference between an agent that can fix the problem and one that
    cannot.

    Copier's own per-file announcements go through `print()`, which resolves
    `sys.stdout` at call time. Under pytest that is a capture object that does not
    write to fd 1 at all, so the fd dup alone silently misses every line and the
    parsed file list comes back empty in exactly the place it is tested.

    Both sinks open the same file with O_APPEND, so writes from either side land at
    the end instead of overwriting each other.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved_out, saved_err = os.dup(1), os.dup(2)
    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    tmp = Path(tempfile.mkstemp(prefix="project-setup-", suffix=".log")[1])
    sink = os.open(tmp, os.O_WRONLY | os.O_APPEND)
    stream = tmp.open("a", buffering=1)
    try:
        os.dup2(sink, 1)
        os.dup2(sink, 2)
        sys.stdout = sys.stderr = stream
        yield tmp
    finally:
        stream.flush()
        sys.stdout, sys.stderr = saved_stdout, saved_stderr
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        stream.close()
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
# Copier announces one line per file it touches. Parsing them is what lets `plan`
# answer the only question a brownfield user has: what of mine gets replaced?
# `conflict` precedes `overwrite` for the same path, so `overwrite` is the signal.
# The operation word arrives wrapped in colour even into a pipe, so strip first.
ANSI = re.compile(r"\x1b\[[0-9;]*m")
OPERATION = re.compile(r"^\s*(create|overwrite|identical|skip|remove|conflict)\s+(\S.*)$")
REPORTED_OPERATIONS = ("overwrite", "create")


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0
    # {operation: paths}, parsed out of Copier's own per-file announcements.
    files: dict[str, list[str]] = field(default_factory=dict)


def split_operations(output: str) -> tuple[dict[str, list[str]], str]:
    """Separate Copier's per-file lines from everything else a layer printed.

    The remainder is a task's own message, which is the part worth echoing; the
    file lines are worth counting.
    """
    files: dict[str, list[str]] = {}
    rest: list[str] = []
    for raw in output.splitlines():
        line = ANSI.sub("", raw)
        if line.startswith("Copying from template version"):
            # Copier's own banner. The version is always None here: layers are
            # plain directories, not tagged template repositories.
            continue
        found = OPERATION.match(line)
        if found is None:
            rest.append(line)
            continue
        paths = files.setdefault(found.group(1), [])
        path = found.group(2).strip()
        if path not in paths:
            paths.append(path)
    return files, "\n".join(rest).strip()


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

    def files(self, operation: str) -> list[str]:
        """Every path the placed layers reported under one Copier operation."""
        seen: list[str] = []
        for step in self.placed:
            for path in step.files.get(operation, ()):
                if path not in seen:
                    seen.append(path)
        return sorted(seen)


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
                    # Copier names every file it touches; place_layers parses those
                    # lines back out so plan can report what it would replace.
                    quiet=False,
                    # Templates are bundled in this repo, so tasks are ours to trust.
                    unsafe=run_tasks,
                    skip_tasks=not run_tasks,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the caller
                error = f"{type(exc).__name__}: {exc}"
        captured = log.read_text().strip()
        log.unlink(missing_ok=True)
        files, output = split_operations(captured)
        if not quiet and output:
            print("\n".join(f"  {line}" for line in output.splitlines()))
        # The task's own message is the useful part; Copier's TaskError only says
        # that the exit status was non-zero.
        detail = "\n".join(part for part in (output, error) if part)
        result.placed.append(
            StepResult(
                name,
                not error,
                detail=detail,
                seconds=time.perf_counter() - started,
                files=files,
            )
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


def generator_args(extra: tuple[str, ...], answers: dict) -> list[str]:
    """Fill a generator's argument template from the effective answer set.

    A missing answer is named rather than raised as a KeyError: the value exists,
    it is just defaulted by a layer instead of supplied, and the caller is expected
    to have merged the catalog defaults in first.
    """
    out: list[str] = []
    for arg in extra:
        if "{" not in arg:
            out.append(arg)
            continue
        try:
            out.append(arg.format(**answers))
        except KeyError as exc:
            raise SystemExit(
                f"generator argument {arg!r} needs answer {exc.args[0]}, which is neither "
                f"supplied nor defaulted by any selected layer"
            ) from exc
    return out


def run_generators(
    dest: Path, result: RunResult, *, data: dict | None = None, quiet: bool = True
) -> RunResult:
    """Run each generator that the placed layers actually installed."""
    answers = data or {}
    for rel, extra in GENERATORS:
        script = dest / rel
        if not script.is_file():
            continue
        args = generator_args(extra, answers)
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
