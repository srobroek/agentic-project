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
        return run(["bun", "init", "-y"])

    if kind == "py":
        if Path("pyproject.toml").exists():
            print("native_init: pyproject.toml present, skipping")
            return 0
        return run(["uv", "init", "--name", arg, "--no-workspace"])

    print(f"native_init: unknown kind {kind!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
