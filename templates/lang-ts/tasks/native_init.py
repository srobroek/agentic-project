#!/usr/bin/env python3
"""Copier task: let the language's own tool own its manifest and lockfile.

    native_init.py rust lib|bin
    native_init.py ts   <project-name>
    native_init.py py   <project-name>:<src|flat>:<python-version>
    native_init.py go   <module-path>

Templating a lockfile is a mistake; the native tool should generate it. Each branch
is a no-op when the manifest already exists, and degrades to a warning when the
tool is absent, so a scaffold never hard-fails on a missing toolchain.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Prefix the scaffolder greps out of a task's captured output. A task that degrades
# instead of failing has to say so somewhere a caller can find it: Copier captures
# every task's output, and `apply` reported `place ok lang-rust` and `95 file(s)
# created` for a Rust repository with no Cargo.toml, because the skip below was
# written into a log nobody printed. Degrading is right; degrading silently is not.
WARNING = "WARNING"


def run(cmd: list[str], owns: str = "") -> int:
    if not shutil.which(cmd[0]):
        lost = f", so {owns} was not created" if owns else ""
        print(
            f"native_init: {WARNING} {cmd[0]} is not on PATH{lost}. Install {cmd[0]} "
            f"and re-run apply; nothing else is missing.",
            file=sys.stderr,
        )
        return 0
    print(f"native_init: {' '.join(cmd)}")
    return subprocess.run(cmd, check=False).returncode


def main() -> int:
    if not 3 <= len(sys.argv) <= 4:
        print(__doc__, file=sys.stderr)
        return 2
    kind, arg = sys.argv[1], sys.argv[2]
    extra = sys.argv[3] if len(sys.argv) == 4 else ""

    # Every reconciliation below is guarded on the manifest actually being there. When
    # the tool is absent `run` degrades to a warning and returns 0, and a tidy pass
    # over a tree the tool never wrote reconciles nothing against nothing.
    if kind == "rust":
        if Path("Cargo.toml").exists():
            print("native_init: Cargo.toml present, skipping")
            return 0
        code = run(["cargo", "init", f"--{arg}", "--quiet"], owns="Cargo.toml")
        if code == 0 and Path("Cargo.toml").is_file():
            _mark_unpublished_if_unlicensed(extra)
        return code

    if kind == "ts":
        if Path("package.json").exists():
            print("native_init: package.json present, skipping")
            return 0
        # What bun drops is only junk if bun is the one who put it there. Record
        # what already exists so the tidy can tell its own leftovers from a file
        # the repository brought with it.
        pre_existing = {name for name, _ in BUN_LEFTOVERS if Path(name).exists()}
        code = run(["bun", "init", "-y"], owns="package.json")
        if code == 0 and Path("package.json").is_file():
            _tidy_after_bun(arg, pre_existing)
            _declare_ts_dev_tools(extra)
            _seed_ts_entry_point()
        return code

    if kind == "py":
        if Path("pyproject.toml").exists():
            print("native_init: pyproject.toml present, skipping")
            return 0
        name, layout, python = arg.split(":", 2)
        pre_existing = {n for n, _ in UV_LEFTOVERS if Path(n).exists()}
        # --lib gives src/<name>/ with py.typed; --app gives the same tree without
        # the packaging intent. Either way uv owns pyproject.toml, and --python is
        # what carries the answered version into requires-python.
        code = run(
            [
                "uv",
                "init",
                "--name",
                name,
                "--no-workspace",
                "--lib" if layout == "src" else "--app",
                "--python",
                python,
            ],
            owns="pyproject.toml",
        )
        if code == 0 and Path("pyproject.toml").is_file():
            _tidy_after_uv(pre_existing)
            _declare_dev_tools()
            _seed_python_test()
        return code

    if kind == "go":
        if Path("go.mod").exists():
            print("native_init: go.mod present, skipping")
            return 0
        code = run(["go", "mod", "init", arg], owns="go.mod")
        if code == 0 and Path("go.mod").is_file():
            _seed_go_package(arg)
        return code

    print(f"native_init: unknown kind {kind!r}", file=sys.stderr)
    return 2


# The one SPDX_ID that is not a licence. Kept here rather than imported: task scripts
# are copied into a layer one file at a time and run standalone.
NO_LICENCE = "NONE"


def _mark_unpublished_if_unlicensed(spdx: str) -> None:
    """Say `publish = false` when the project states no licence.

    `cargo init` writes no `license` field, so cargo-deny falls back to reading the
    LICENSE file -- which a licensed project has. With `SPDX_ID=NONE` there is none,
    and `cargo deny check licenses` then fails the crate itself as unlicensed:
    `error[unlicensed]: <name> is unlicensed`. Measured: the `[licenses.private]`
    block deny.toml carries for this case only applies to a crate the manifest marks
    unpublishable, which an unlicensed crate is anyway.
    """
    if spdx != NO_LICENCE:
        return
    manifest = Path("Cargo.toml")
    body = manifest.read_text()
    if "publish" in body:
        return
    manifest.write_text(
        body.replace('version = "0.1.0"\n', 'version = "0.1.0"\npublish = false\n', 1)
    )
    print("native_init: marked the crate publish = false; it states no licence")


# What `bun init -y` writes that a layer owns or that a fresh repository should not
# carry. Each is removed only when bun created it in this run.
#
# index.ts is deliberately absent: package.json names it as the module entry, and
# deleting it left `tsc --noEmit` failing with TS18003 "no inputs were found",
# `bun test` with "0 test files", and knip reporting a missing entry file. Its
# contents are replaced instead, which is the same call made for uv's placeholder.
BUN_LEFTOVERS: tuple[tuple[str, str], ...] = (
    ("CLAUDE.md", "the steering layer owns agent instructions"),
    (".gitignore", "fold_gitignore.py builds it from .gitignore.d/ fragments"),
    # bun 1.4 writes a lockfile during `init`, before this task renames the package
    # and adds the tool dependencies. `just setup` then runs
    # `bun install --frozen-lockfile`, which failed with "lockfile had changes, but
    # lockfile is frozen" on a scaffold nobody had touched. The first setup writes a
    # lockfile that matches the manifest.
    ("bun.lock", "it predates this task's changes to package.json"),
)


def _tidy_after_bun(project_name: str, pre_existing: set[str]) -> None:
    """Reconcile what `bun init` writes with what the layers own.

    `bun init -y` takes no name argument, so it names the package after the
    directory. It also drops a generic CLAUDE.md, its own .gitignore and a
    placeholder index.ts. The steering layer owns CLAUDE.md and refuses to
    overwrite one it did not write, which failed an otherwise clean apply; the
    .gitignore would be adopted as unmanaged text above the generated block
    forever; and the placeholder is junk in a fresh repository.

    Anything that existed before `bun init` ran is left alone. Deleting by filename
    would take a brownfield repository's own CLAUDE.md with it.
    """
    manifest = Path("package.json")
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text())
        except json.JSONDecodeError:
            print("native_init: package.json is not valid JSON, leaving it", file=sys.stderr)
        else:
            if data.get("name") != project_name:
                data["name"] = project_name
                manifest.write_text(json.dumps(data, indent=2) + "\n")
                print(f"native_init: set package.json name to {project_name}")

    for name, reason in BUN_LEFTOVERS:
        path = Path(name)
        if name in pre_existing or not path.is_file() or path.is_symlink():
            continue
        path.unlink()
        print(f"native_init: removed bun's {name}; {reason}")


TS_ENTRY = """export function greet(name: string): string {
  return `Hello, ${name}`;
}
"""

TS_ENTRY_TEST = """import { expect, test } from "bun:test";

import { greet } from "./index";

test("greet names the caller", () => {
  expect(greet("world")).toBe("Hello, world");
});
"""


def _declare_ts_dev_tools(pinned: str) -> None:
    """Add the tools the ts recipes call, which `bun init` does not.

    Two failures come from this being absent. `bunx <tool>` with nothing installed
    locally falls through to PATH, so a mise shim answered for biome and the run died
    on a version nobody set. And `.oxlintrc.json` sets `typeAware: true`, which makes
    oxlint refuse to start without oxlint-tsgolint: `just check` failed on every ts
    scaffold whatever its sources. Written into package.json rather than installed
    here, so apply needs no network; `just setup` resolves and locks them.
    """
    entries = dict(item.split("=", 1) for item in pinned.split(",") if "=" in item)
    manifest = Path("package.json")
    if not entries or not manifest.is_file():
        return
    try:
        data = json.loads(manifest.read_text())
    except json.JSONDecodeError:
        print("native_init: package.json is not valid JSON, leaving it", file=sys.stderr)
        return
    dev = data.setdefault("devDependencies", {})
    added = {name: version for name, version in entries.items() if name not in dev}
    if not added:
        return
    dev.update(added)
    data["devDependencies"] = dict(sorted(dev.items()))
    manifest.write_text(json.dumps(data, indent=2) + "\n")
    print(f"native_init: added {len(added)} dev tool(s) the just recipes call")


def _seed_ts_entry_point() -> None:
    """Replace bun's placeholder with a module, and give it one test.

    `bun init` writes `console.log("Hello via Bun!")`, which is not an entry point,
    and package.json names index.ts as the module. Deleting it broke three recipes at
    once, so the file stays and its contents are ours. The test exists because
    `bun test` exits 1 on an empty run.
    """
    entry = Path("index.ts")
    if entry.is_file() and "Hello via Bun" not in entry.read_text():
        return
    entry.write_text(TS_ENTRY)
    print("native_init: replaced bun's placeholder index.ts with a module")
    test = Path("index.test.ts")
    if not test.exists():
        test.write_text(TS_ENTRY_TEST)
        print(f"native_init: wrote {test} so an empty test run does not fail the gate")


# What `uv init` writes that a layer already owns. Same rule as bun's: removed only
# when uv created it in this run.
UV_LEFTOVERS: tuple[tuple[str, str], ...] = (
    (".python-version", ".mise/conf.d/python.toml already pins the interpreter"),
)

SMOKE_TEST = '''"""The packaging itself is the thing under test here.

Delete this once the package has tests of its own. It exists because `pytest` exits
5 on an empty test run, so a scaffold without it fails `just check` before anybody
has written a line.
"""

from __future__ import annotations

import {module}


def test_the_package_imports() -> None:
    assert {module}.__name__ == "{module}"
'''

GO_MAIN = """package main

import "fmt"

func main() {
\tfmt.Println("%s")
}
"""


def _tidy_after_uv(pre_existing: set[str]) -> None:
    """Drop the interpreter pin uv writes, which mise already owns.

    Two files pinning the same interpreter is a trap: `.python-version` and
    `.mise/conf.d/python.toml` drift apart the moment either is edited, and mise
    reads both. uv's pyproject `requires-python` stays, because that is the answer
    the user gave.
    """
    for name, reason in UV_LEFTOVERS:
        path = Path(name)
        if name in pre_existing or not path.is_file() or path.is_symlink():
            continue
        path.unlink()
        print(f"native_init: removed uv's {name}; {reason}")
    _strip_uv_placeholder()


def _strip_uv_placeholder() -> None:
    """Leave the package importable and empty, not carrying uv's `hello()`.

    `uv init --lib` writes a `def hello()` returning "Hello from <name>!", which the
    layer's own ruff config then rejects: `__init__` is for docstrings and
    re-exports. So an untouched scaffold failed `just check` on a function the user
    never wrote. Same call as bun's placeholder index.ts, which is deleted outright;
    here the file has to stay for the package to exist.
    """
    for init in Path("src").glob("*/__init__.py"):
        text = init.read_text()
        if "Hello from" not in text:
            continue
        init.write_text(f'"""{init.parent.name}."""\n')
        print(f"native_init: emptied uv's placeholder {init}; __init__ holds no logic")


# Every tool a python recipe invokes with `uv run`. Unversioned on purpose: uv.lock
# is the pin, the layer's .gitignore keeps it, and Renovate updates it. Templating a
# version here would put a second pin beside the lockfile's.
DEV_TOOLS: tuple[str, ...] = ("ruff", "ty", "pytest", "pytest-cov", "deptry", "nox")


def _declare_dev_tools() -> None:
    """Put the tools the recipes call into pyproject, because nothing else does.

    `uv init` writes `dependencies = []` and no group, so `uv run ruff` and
    `uv run ty` resolved to whatever happened to be on PATH, or failed outright:
    `just check` died on "Failed to spawn: ty" in a fresh scaffold, and CI's
    `uv sync --frozen` would have installed nothing either. Appended as text rather
    than through `uv add`, which resolves over the network and would make a scaffold
    fail offline.
    """
    manifest = Path("pyproject.toml")
    text = manifest.read_text()
    if "[dependency-groups]" in text:
        return
    listed = "\n".join(f'    "{name}",' for name in DEV_TOOLS)
    manifest.write_text(f"{text.rstrip(chr(10))}\n\n[dependency-groups]\ndev = [\n{listed}\n]\n")
    print(f"native_init: declared {len(DEV_TOOLS)} dev tool(s) the just recipes call")


def _seed_python_test() -> None:
    """Give the package one test, because an empty pytest run is a failure.

    `pytest` exits 5 when it collects nothing and 1 when `testpaths` does not
    exist, and the layer's pytest.ini turns that warning into an error. Either way
    `just check` is red on a scaffold nobody has touched yet.
    """
    packages = sorted(p for p in Path("src").glob("*") if (p / "__init__.py").is_file())
    tests = Path("tests")
    if not packages or any(tests.glob("test_*.py")):
        return
    tests.mkdir(exist_ok=True)
    target = tests / "test_smoke.py"
    target.write_text(SMOKE_TEST.format(module=packages[0].name))
    print(f"native_init: wrote {target} so an empty test run does not fail the gate")


def _seed_go_package(module: str) -> None:
    """Give the module one package, because `go mod init` writes no source.

    `cargo init` and `bun init` both seed an entry point; Go's own tool does not.
    Without one, `go test ./...` exits 1 and `golangci-lint run ./...` exits 5 with
    "no go files to analyze", so a fresh scaffold fails its own gate.
    """
    if any(Path().rglob("*.go")):
        return
    Path("main.go").write_text(GO_MAIN % module)
    print("native_init: wrote main.go so the module has a package to build and lint")


if __name__ == "__main__":
    raise SystemExit(main())
