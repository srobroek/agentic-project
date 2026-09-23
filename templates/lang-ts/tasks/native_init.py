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
        code = run(["bun", "init", "-y"])
        if code == 0:
            _tidy_after_bun(arg)
        return code

    if kind == "py":
        if Path("pyproject.toml").exists():
            print("native_init: pyproject.toml present, skipping")
            return 0
        return run(["uv", "init", "--name", arg, "--no-workspace"])

    print(f"native_init: unknown kind {kind!r}", file=sys.stderr)
    return 2


def _tidy_after_bun(project_name: str) -> None:
    """Reconcile what `bun init` writes with what the layers own.

    `bun init -y` takes no name argument, so it names the package after the directory,
    and it drops a generic CLAUDE.md. The steering layer owns agent instructions and
    refuses to overwrite a CLAUDE.md it did not write, which would fail the whole
    apply on an otherwise clean scaffold.
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

    claude = Path("CLAUDE.md")
    if claude.is_file() and not claude.is_symlink():
        claude.unlink()
        print("native_init: removed bun's CLAUDE.md; the steering layer owns that file")


if __name__ == "__main__":
    raise SystemExit(main())
