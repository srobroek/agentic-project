#!/usr/bin/env python3
"""Create an AWS CDK v2 TypeScript application with the native CDK generator."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", required=True, help="new repository-relative CDK member")
    parser.add_argument("--cdk-version", required=True, help="exact stable aws-cdk version")
    return parser.parse_args()


def destination(root: Path, value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute() or not raw.parts or any(part in {"", ".", ".."} for part in raw.parts):
        raise SystemExit("--dest must be a non-empty repository-relative path without dot segments")
    resolved = (root / raw).resolve()
    if root not in resolved.parents or not NAME.fullmatch(raw.name):
        raise SystemExit("--dest must stay under the repository and end in an identifier-like name")
    if resolved.exists():
        raise SystemExit(f"destination already exists: {raw}")
    return resolved


# What `cdk init` writes because its template is an npm package, and this is not one.
# Removed so a fresh repository does not ship a publish rule for an app nobody
# publishes.
LEFTOVERS: tuple[str, ...] = (".npmignore",)


def run(command: list[str], *, cwd: Path, what: str) -> None:
    """Run a native tool, and say which one failed rather than raising a traceback."""
    if shutil.which(command[0]) is None:
        raise SystemExit(f"{command[0]} is not on PATH, so {what} cannot run")
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"{what} failed (exit {result.returncode}): {' '.join(command)}\n"
            f"  Its own output is above. A version that does not exist is the usual "
            f"cause; pick one the registry lists."
        )


def main() -> None:
    args = parse_args()
    if not VERSION.fullmatch(args.cdk_version):
        raise SystemExit("--cdk-version must be an exact stable X.Y.Z version")

    root = Path.cwd().resolve()
    dest = destination(root, args.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".aws-cdk-init-", dir=dest.parent) as temporary:
        work = Path(temporary) / dest.name
        work.mkdir()
        command = [
            "bunx",
            "--package",
            f"aws-cdk@{args.cdk_version}",
            "cdk",
            "init",
            "app",
            "--language",
            "typescript",
            "--generate-only",
        ]
        run(command, cwd=work, what=f"cdk init with aws-cdk@{args.cdk_version}")
        run(["bun", "install"], cwd=work, what="bun install for the generated app")
        removed = []
        for name in LEFTOVERS:
            leftover = work / name
            if leftover.is_file():
                leftover.unlink()
                removed.append(name)
        os.replace(work, dest)

    print(
        json.dumps(
            {
                "cdk_version": args.cdk_version,
                "destination": str(dest.relative_to(root)),
                "generated": True,
                "removed": removed,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
