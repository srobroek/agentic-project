"""Place layers and run the fragment generators.

Copier is driven in-process rather than by subprocess: one interpreter start for a
whole scaffold instead of one per layer.
"""

from __future__ import annotations

import contextlib
import json
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
import yaml

from .catalog import ANSWERS_FILE, Catalog, selected_layers, want_var


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
# What a generator writes is not declared here. `plan` rehearses the whole apply in a
# copy of the destination and reports the difference, because every static table of
# "what a generator does to this path" has been wrong in use: it listed the justfile
# as both overwritten and merged, and promised "replaced outright" for a ci.yml the
# generator would never touch.
GENERATORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scripts/fold_gitignore.py", ()),
    ("scripts/merge_hooks.py", ()),
    ("scripts/gen_justfile.py", ()),
    # --keep-hand-owned is what the script's own help calls "for a render that must not
    # fail", and this is that render. Without it a brownfield repository with its own
    # .github/workflows/ci.yml made the generator exit 3, which marked the whole apply
    # FAIL and suppressed the placeholder report over a scaffold that was otherwise
    # complete. Leaving somebody's CI alone is the correct outcome, not a failure.
    ("scripts/gen_caller.py", ("--default-branch", "{DEFAULT_BRANCH}", "--keep-hand-owned")),
    ("scripts/gen_steering.py", ()),
    ("scripts/install_agents_index.py", ()),
)
# Not ours, never scanned and never copied into a rehearsal. Native init populates
# them, they can be enormous, and nothing here writes into them.
SCAN_SKIP = frozenset({".git", "node_modules", ".venv", "target", "dist", "__pycache__"})
# Present from the moment apply starts writing until it has finished, so a run that
# was killed, or failed a step, is visible in the checkout rather than looking like
# a scaffold. Re-running apply finishes the job: every step is idempotent.
INCOMPLETE_FILE = ".project-setup-incomplete"
# Copier announces one line per file it touches. Parsing them is what lets `plan`
# answer the only question a brownfield user has: what of mine gets replaced?
# `conflict` precedes `overwrite` for the same path, so `overwrite` is the signal.
# The operation word arrives wrapped in colour even into a pipe, so strip first.
ANSI = re.compile(r"\x1b\[[0-9;]*m")
OPERATION = re.compile(r"^\s*(create|overwrite|identical|skip|remove|conflict)\s+(\S.*)$")
REPORTED_OPERATIONS = ("overwrite", "create")
# A task that degraded instead of failing. Copier captures task output, so `apply`
# reported `place ok lang-rust` and `95 file(s) created` for a Rust repository with no
# Cargo.toml: `cargo` was absent, the task skipped as designed, and the skip went into
# a log nobody printed. Degrading is the right trade; degrading invisibly is not, so
# every task marks one with this token and the summary carries it.
TASK_WARNING = re.compile(r"^\s*([\w.-]+): WARNING (.+)$")


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0
    # {operation: paths}, parsed out of Copier's own per-file announcements.
    files: dict[str, list[str]] = field(default_factory=dict)
    # Degradations a task reported and carried on from.
    warnings: list[str] = field(default_factory=list)


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


def task_warnings(output: str) -> list[str]:
    """Every degradation a task marked, without its marker."""
    found: list[str] = []
    for line in output.splitlines():
        marked = TASK_WARNING.match(line)
        if marked is not None:
            found.append(f"{marked.group(1)}: {marked.group(2).strip()}")
    return found


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

    @property
    def warnings(self) -> list[tuple[str, str]]:
        """(step, message) for every degradation, in the order they happened."""
        return [(s.name, w) for s in self.placed + self.generated for w in s.warnings]


def place_layers(
    catalog: Catalog,
    dest: Path,
    data: dict,
    *,
    pretend: bool = False,
    run_tasks: bool = True,
    quiet: bool = True,
    layers: list[str] | None = None,
) -> RunResult:
    # An explicit list is for asking what one layer on its own would write. The
    # answers always select the always-on set too, which is not an answer to that.
    layers = selected_layers(catalog, data) if layers is None else layers
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
                warnings=task_warnings(output),
            )
        )
        if error:
            break
    return result


def prune_empty_dirs(dest: Path, keep: frozenset[str] = frozenset()) -> list[str]:
    """Remove directories left empty because their contents were excluded.

    Copier creates a directory before deciding that every file inside it is
    excluded, so a forge the project does not use still leaves an empty `.gitlab/`
    behind. Deepest-first so nested shells collapse in one pass.

    Only a directory this run created goes. `keep` is every directory that existed
    beforehand: a brownfield repository's own empty `logs/`, and every empty
    directory inside node_modules, used to be deleted without a word.
    """
    removed: list[str] = []
    for path in sorted(
        (Path(root) for root, _dirs, _files in walk(dest) if Path(root) != dest),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        relative = path.relative_to(dest).as_posix()
        if relative in keep or any(path.iterdir()):
            continue
        path.rmdir()
        removed.append(relative)
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


def walk(root: Path) -> Iterator[tuple[str, list[str], list[str]]]:
    """`os.walk`, never descending into SCAN_SKIP, and never following a link."""
    for current, dirs, files in os.walk(root):
        kept = []
        for name in dirs:
            if name in SCAN_SKIP:
                continue
            if os.path.islink(os.path.join(current, name)):
                # A link to a directory is an entry, not a tree to scan.
                files.append(name)
                continue
            kept.append(name)
        dirs[:] = kept
        yield current, dirs, files


@dataclass(frozen=True)
class Snapshot:
    """Every path apply could touch: file bytes, or a link's target as a string.

    `through` is what a reader of each link saw, wherever it points, because a
    link's content is what somebody loses when it is replaced.
    """

    files: dict[str, bytes | str]
    dirs: frozenset[str]
    through: dict[str, bytes] = field(default_factory=dict, compare=False)

    def text(self, relative: str) -> str:
        entry = self.files.get(relative)
        if isinstance(entry, str):
            entry = self.through.get(relative)
        return entry.decode("utf-8", "replace") if isinstance(entry, bytes) else ""


def snapshot(root: Path) -> Snapshot:
    files: dict[str, bytes | str] = {}
    through: dict[str, bytes] = {}
    dirs: set[str] = set()
    if not root.is_dir():
        return Snapshot(files, frozenset())
    for current, subdirs, names in walk(root):
        base = Path(current)
        for name in subdirs:
            dirs.add((base / name).relative_to(root).as_posix())
        for name in names:
            path = base / name
            relative = path.relative_to(root).as_posix()
            if relative == INCOMPLETE_FILE:
                continue
            if path.is_symlink():
                files[relative] = os.readlink(path)
                if path.is_file():
                    through[relative] = path.read_bytes()
            elif path.is_file():
                files[relative] = path.read_bytes()
    return Snapshot(files, frozenset(dirs), through)


@dataclass
class Changes:
    """What a run did to a destination, classified by what the owner loses.

    `merge` is a changed file that still holds every non-blank line it held before;
    `overwrite` is one that does not, with the count in `lost`. Derived from the two
    snapshots, never from a table of what each step is expected to do.
    """

    create: list[str] = field(default_factory=list)
    overwrite: list[str] = field(default_factory=list)
    merge: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    lost: dict[str, int] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict:
        return {
            "create": self.create,
            "overwrite": self.overwrite,
            "merge": self.merge,
            "remove": self.remove,
            "lines_lost": self.lost,
            "links": self.links,
        }


def classify(before: Snapshot, after: Snapshot) -> Changes:
    changes = Changes()
    for relative in sorted(set(before.files) | set(after.files)):
        old, new = before.files.get(relative), after.files.get(relative)
        # The same link can still have changed: a step that writes through a link
        # rewrites whatever it points at.
        if old == new and (
            not isinstance(old, str) or before.through.get(relative) == after.through.get(relative)
        ):
            continue
        if old is None:
            changes.create.append(relative)
            continue
        if new is None:
            changes.remove.append(relative)
            continue
        if isinstance(new, str):
            changes.links[relative] = new
        kept = set(after.text(relative).splitlines())
        lost = [
            line for line in before.text(relative).splitlines() if line.strip() and line not in kept
        ]
        if lost:
            changes.overwrite.append(relative)
            changes.lost[relative] = len(lost)
        else:
            changes.merge.append(relative)
    return changes


def scaffold(
    catalog: Catalog,
    dest: Path,
    data: dict,
    *,
    keep_dirs: frozenset[str],
    run_tasks: bool = True,
    quiet: bool = True,
    layers: list[str] | None = None,
) -> RunResult:
    """Everything apply does to a destination, in order, and the one place it is done.

    `plan` calls this too, on a copy, which is what makes the plan the apply. `layers`
    is how a member-scoped apply narrows this to the layers it selected: `None` means
    the ordinary case, every always-on layer plus whatever the answers select.
    """
    dest.mkdir(parents=True, exist_ok=True)
    marker = dest / INCOMPLETE_FILE
    marker.write_text(
        "project-setup apply started here and has not finished: it was interrupted, or\n"
        "a step failed. The files it placed are real, but the scaffold is incomplete.\n"
        "Re-run the same apply to finish it; every step is safe to repeat.\n"
    )
    result = place_layers(catalog, dest, data, run_tasks=run_tasks, quiet=quiet, layers=layers)
    if result.ok:
        prune_empty_dirs(dest, keep=keep_dirs)
        run_generators(
            dest,
            result,
            # Copier applies a layer's defaults itself; a generator argument is
            # assembled out here and would otherwise miss every unanswered one.
            data={**catalog.defaults_for(result.layers), **data},
            quiet=True,
        )
    if result.ok:
        # `layers` is only ever narrowed for a member-scoped apply (see cli.py's
        # `--member`), so its presence is the signal `find_scaffold_root` uses to
        # skip this file rather than mistake this member for the root above it.
        recorded = {**data, "_MEMBER": True} if layers is not None else data
        (dest / ANSWERS_FILE).write_text(
            "# Written by project-setup apply. Re-run with --data-file to reproduce.\n"
            + yaml.safe_dump(recorded, sort_keys=True)
        )
        marker.unlink()
    return result


def rehearse(
    catalog: Catalog, dest: Path, data: dict, *, layers: list[str] | None = None
) -> tuple[RunResult, Changes]:
    """Run the real apply, tasks and generators included, in a copy of `dest`.

    The difference between the copy before and after is the plan. Nothing is
    predicted, so there is nothing for the plan to get wrong that the apply would
    not get wrong identically. What the copy leaves out is SCAN_SKIP, which no step
    writes into. A link that points outside the destination is copied as the file
    it points at, so a step writing through it writes into the copy.
    """
    before = snapshot(dest)
    with tempfile.TemporaryDirectory(prefix="project-setup-plan-") as scratch:
        # The same name, because a native tool may read it: `cargo init` names a
        # crate after its directory when it is not told otherwise.
        stage = Path(scratch) / (dest.resolve().name or "repo")
        outside = _copy_for_rehearsal(dest, stage)
        result = scaffold(catalog, stage, data, keep_dirs=before.dirs, quiet=True, layers=layers)
        after = snapshot(stage)
        for relative, (target, copied) in outside.items():
            if after.files.get(relative) == copied:
                after.files[relative] = target
                if copied is not None:
                    after.through[relative] = copied
        for step in result.placed + result.generated:
            step.detail = step.detail.replace(str(stage), str(dest))
            step.warnings = [w.replace(str(stage), str(dest)) for w in step.warnings]
    result.dest, result.pretend = dest, True
    return result, classify(before, after)


def _copy_for_rehearsal(dest: Path, stage: Path) -> dict[str, tuple[str, bytes | None]]:
    """Copy dest into stage. Returns each link that points outside dest: its target,
    and the bytes that stand in for it in the copy (None when nothing could)."""
    outside: dict[str, tuple[str, bytes | None]] = {}
    stage.mkdir(parents=True)
    if not dest.is_dir():
        return outside
    root = dest.resolve()
    for current, dirs, files in walk(dest):
        base = Path(current)
        target_dir = stage / base.relative_to(dest)
        for name in dirs:
            (target_dir / name).mkdir()
        for name in files:
            path = base / name
            copy = target_dir / name
            if path.is_symlink():
                resolved = path.resolve()
                if resolved == root or root in resolved.parents:
                    copy.symlink_to(os.readlink(path))
                    continue
                copied = resolved.read_bytes() if resolved.is_file() else None
                if copied is not None:
                    copy.write_bytes(copied)
                outside[path.relative_to(dest).as_posix()] = (os.readlink(path), copied)
            elif path.is_file():
                copy.write_bytes(path.read_bytes())
                copy.chmod(path.stat().st_mode)
    return outside


def deselected_layers(catalog: Catalog, dest: Path, data: dict) -> list[str]:
    """Layers a previous apply selected and this answer set does not.

    The destination's own answers file is the record of what was selected last time,
    so this needs no manifest and no guessing. A layer that was never applied is not
    deselected, it is simply absent.
    """
    from .catalog import ANSWERS_FILE

    recorded_file = dest / ANSWERS_FILE
    if not recorded_file.is_file():
        return []
    try:
        recorded = yaml.safe_load(recorded_file.read_text()) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(recorded, dict):
        return []
    before = set(selected_layers(catalog, recorded))
    now = set(selected_layers(catalog, data))
    return sorted(before - now)


def orphaned_files(
    catalog: Catalog, dest: Path, layers: list[str], data: dict | None = None
) -> dict[str, list[str]]:
    """Files each named layer would place, which are still present in the destination.

    Copier excludes what a layer no longer contributes; it does not delete what an
    earlier run already wrote. So turning a layer off left its whole output in place,
    exactly as switching `FORGE_PLATFORM` did before `_stale_forge_surface`.

    The file list comes from a pretend place into a scratch directory rather than a
    static walk of the layer. A layer's paths are templated and conditionally
    excluded, so the only list that is right by construction is the one Copier itself
    produces. It costs one dry run per deselected layer, which is why the caller
    establishes there is a deselected layer first.

    One layer at a time, explicitly: the answer set selects the always-on layers too,
    and their files are not what this is asking about. The real answers are merged in
    so a templated path resolves the way it resolved when it was written, and the two
    required questions are filled because a probe that cannot render lists nothing --
    which reads as "no leftovers" and is the failure this function exists to avoid.
    """
    found: dict[str, list[str]] = {}
    for name in layers:
        answers = {
            "PROJECT_NAME": "orphan-probe",
            "DESCRIPTION": "orphan probe",
            **catalog.defaults_for([name]),
            **(data or {}),
            want_var(name): True,
        }
        with tempfile.TemporaryDirectory(prefix="project-setup-orphans-") as scratch:
            probe = place_layers(
                catalog,
                Path(scratch),
                answers,
                pretend=True,
                run_tasks=False,
                layers=[name],
            )
        if not probe.ok:
            continue
        still_here = [
            relative
            for relative in probe.files("create")
            if (dest / relative).is_symlink() or (dest / relative).exists()
        ]
        if still_here:
            found[name] = still_here
    return found


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
                warnings=task_warnings(detail),
            )
        )
        if not quiet and detail:
            print(f"  {rel}: {detail}")
    return result


# What the ci layer renders MONOREPO_MEMBERS into, byte for byte:
# assets/ci/.ci/members.json.template is `{"members": @@MONOREPO_MEMBERS@@}`. A
# member-scoped apply rewrites this one file directly rather than re-running the
# whole ci layer at the root, which would also rewrite every other file that layer
# owns -- the root's own generated files are not this apply's to touch.
MEMBERS_JSON = ".ci/members.json"


def register_member(root: Path, name: str, path: str, capabilities: dict[str, list[str]]) -> str:
    """Upsert one member into the root scaffold's MONOREPO_MEMBERS, by path.

    Rewrites `.ci/members.json` -- what gen_caller.py actually reads -- and the
    root's own recorded answer, so a later full re-apply at the root reproduces the
    same member list instead of the two example members `monorepo.yml` shipped.
    """
    answers_file = root / ANSWERS_FILE
    recorded = (yaml.safe_load(answers_file.read_text()) if answers_file.is_file() else {}) or {}
    raw = recorded.get("MONOREPO_MEMBERS", "[]")
    members = json.loads(raw) if isinstance(raw, str) else list(raw or [])
    members = [m for m in members if m.get("path") != path]
    members.append({"name": name, "path": path, "capabilities": capabilities})
    members.sort(key=lambda m: m["path"])

    recorded["MONOREPO_MEMBERS"] = json.dumps(members)
    answers_file.write_text(
        "# Written by project-setup apply. Re-run with --data-file to reproduce.\n"
        + yaml.safe_dump(recorded, sort_keys=True)
    )
    members_file = root / MEMBERS_JSON
    members_file.parent.mkdir(parents=True, exist_ok=True)
    members_file.write_text(json.dumps({"members": members}, indent=2) + "\n")
    return f"registered {name!r} at {path} in {members_file}"
