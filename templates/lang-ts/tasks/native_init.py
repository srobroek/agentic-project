#!/usr/bin/env python3
"""Copier task: let the language's own tool own its manifest and lockfile.

    native_init.py rust lib|bin
    native_init.py ts   <project-name>
    native_init.py py   <project-name>

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


def run(cmd: list[str]) -> int:
    if not shutil.which(cmd[0]):
        print(f"native_init: {cmd[0]} not on PATH; skipping {' '.join(cmd)}", file=sys.stderr)
        return 0
    print(f"native_init: {' '.join(cmd)}")
    return subprocess.run(cmd, check=False).returncode


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    kind, arg = sys.argv[1], sys.argv[2]

    if kind == "rust":
        if Path("Cargo.toml").exists():
            print("native_init: Cargo.toml present, skipping")
            return 0
        return run(["cargo", "init", f"--{arg}", "--quiet"])

    if kind == "ts":
        if Path("package.json").exists():
            print("native_init: package.json present, skipping")
            return 0
        # What bun drops is only junk if bun is the one who put it there. Record
        # what already exists so the tidy can tell its own leftovers from a file
        # the repository brought with it.
        pre_existing = {name for name, _ in BUN_LEFTOVERS if Path(name).exists()}
        code = run(["bun", "init", "-y"])
        if code == 0:
            _tidy_after_bun(arg, pre_existing)
        return code

    if kind == "py":
        if Path("pyproject.toml").exists():
            print("native_init: pyproject.toml present, skipping")
            return 0
        return run(["uv", "init", "--name", arg, "--no-workspace"])

    print(f"native_init: unknown kind {kind!r}", file=sys.stderr)
    return 2


# What `bun init -y` writes that a layer owns or that a fresh repository should not
# carry. Each is removed only when bun created it in this run.
BUN_LEFTOVERS: tuple[tuple[str, str], ...] = (
    ("CLAUDE.md", "the steering layer owns agent instructions"),
    (".gitignore", "fold_gitignore.py builds it from .gitignore.d/ fragments"),
    ("index.ts", "a Hello-via-Bun placeholder is not this project's entry point"),
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


if __name__ == "__main__":
    raise SystemExit(main())
