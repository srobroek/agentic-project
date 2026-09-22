#!/usr/bin/env python3
"""Copier task: make the destination a git repo, idempotently.

Runs in the destination directory. Does nothing if a repo already exists, so a
brownfield apply never touches history.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if Path(".git").exists():
        print("git_init: repository already present, nothing to do")
        return 0
    if not shutil_which("git"):
        print("git_init: git not on PATH; skipping", file=sys.stderr)
        return 0
    subprocess.run(["git", "init", "-q"], check=True)
    print("git_init: initialised empty repository")
    return 0


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


if __name__ == "__main__":
    raise SystemExit(main())
