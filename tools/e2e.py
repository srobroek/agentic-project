#!/usr/bin/env python3
"""End-to-end check: drive the real CLI over every preset and verify the result.

    python3 tools/e2e.py [--keep] [--preset NAME]

Unlike the unit tests, this shells out to the installed `project-setup` command with
tasks enabled, so it covers the path a user or an agent actually takes: validate,
plan, apply, re-apply. Native toolchain init is not a question and is not suppressed:
each branch skips when its manifest exists and warns when its tool is absent, so the
run needs no toolchain but uses one when it is there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from project_setup.cli import load_preset

REPO = Path(__file__).resolve().parents[1]
PRESETS = REPO / "presets"
CLI = REPO / ".venv/bin/project-setup"

# Only these two have no default: everything else is defaulted or placeholdered, so a
# scaffold is never blocked on a value nobody knows yet.
IDENTITY = {
    "PROJECT_NAME": "e2e-app",
    "DESCRIPTION": "End-to-end verification project",
}

# Nothing to fill: a preset plus the two identity answers is a complete answer set.
# A preset that needs more than that is a preset bug, and this run will surface it.
GAP_FILLERS: dict[str, dict[str, str]] = {}

# What each preset must produce. One probe per selected capability is enough to catch
# a layer that silently stopped being placed.
#
# The manifest a native init writes is listed for every language, because two of them
# shipped no manifest and wired no task at all: `just setup` failed on "No
# `pyproject.toml` found" and "go: no modules specified" in presets this suite
# reported as ok.
EXPECTED: dict[str, list[str]] = {
    "minimal": ["LICENSE", "justfile", ".pre-commit-config.yaml", "AGENTS.md", "CLAUDE.md"],
    "ts-service": [
        "tsconfig.json",
        "biome.json",
        ".just.d/ts.just",
        "renovate.json",
        "package.json",
        "index.ts",
        "index.test.ts",
    ],
    "go-service": [
        ".golangci.yml",
        ".just.d/go.just",
        ".github/workflows/ci.yml",
        "go.mod",
        "main.go",
    ],
    "rust-cli": [
        "rustfmt.toml",
        "clippy.toml",
        "deny.toml",
        "rust-toolchain.toml",
        "Cargo.toml",
        "src/main.rs",
    ],
    "py-lib": [
        "ruff.toml",
        "pytest.ini",
        ".just.d/python.just",
        "ty.toml",
        "pyproject.toml",
        "tests/test_smoke.py",
    ],
    "polyglot-service": [".golangci.yml", "tsconfig.json", ".config/wt.toml", "go.mod"],
    "gitlab-service": [".gitlab-ci.yml", ".gitlab/ci/go.yml", "go.mod"],
    "monorepo": [".ci/members.json", ".just.d/go.just", ".just.d/ts.just"],
    "api-service": ["openapi.yaml", ".just.d/api.just"],
    "web-app": [
        "project.inlang/settings.json",
        ".a11y/playwright.config.ts",
        "scripts/init_aws_cdk.py",
    ],
}

# Files that must NOT exist, because the preset excludes them.
FORBIDDEN: dict[str, list[str]] = {
    "gitlab-service": [".github", ".github/workflows/ci.yml"],
    "minimal": [".gitlab", "tsconfig.json", ".ci/members.json"],
    "go-service": [".gitlab", "tsconfig.json"],
}


# Not ours, and not scanned: native init populates these, and third-party files
# legitimately contain the token delimiter.
VENDOR = {".git", "node_modules", ".venv", "target", "dist", "__pycache__"}


def ours(path: Path, root: Path) -> bool:
    return not (VENDOR & set(path.relative_to(root).parts))


class Failure(Exception):
    pass


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(CLI), *args], capture_output=True, text=True, check=False, cwd=REPO)


def answers_for(preset: str) -> dict:
    data = load_preset(preset, PRESETS)[0]
    data.update(IDENTITY)
    data.update(GAP_FILLERS.get(preset, {}))
    return data


def fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ours(path, root):
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def check_preset(preset: str, workdir: Path) -> dict:
    dest = workdir / preset
    dest.mkdir(parents=True)
    answer_file = workdir / f"{preset}.yml"
    answer_file.write_text(yaml.safe_dump(answers_for(preset), sort_keys=True))
    common = ["--data-file", str(answer_file)]

    # 1. validate -- must be clean, and machine-readable
    proc = run(["validate", *common, "--json"])
    if proc.returncode != 0:
        raise Failure(f"validate failed: {proc.stdout.strip() or proc.stderr.strip()}")
    report = json.loads(proc.stdout)
    if not report["ok"]:
        raise Failure(f"validate reported problems: {report['problems']}")

    # 2. plan -- must write nothing
    proc = run(["plan", *common, "--dest", str(dest), "--json"])
    if proc.returncode != 0:
        raise Failure(f"plan failed: {proc.stderr.strip()[:300]}")
    if any(dest.iterdir()):
        raise Failure("plan wrote files; a dry run must not touch the destination")

    # 3. apply, with tasks
    started = time.perf_counter()
    proc = run(["apply", *common, "--dest", str(dest), "--json"])
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        raise Failure(f"apply failed: {proc.stdout.strip()[:400] or proc.stderr.strip()[:400]}")
    result = json.loads(proc.stdout)
    if not result["ok"]:
        broken = [s for s in result["placed"] + result["generated"] if not s["ok"]]
        raise Failure(f"apply reported failures: {broken}")

    # 4. no unresolved tokens anywhere
    leftovers = [
        str(p.relative_to(dest))
        for p in dest.rglob("*")
        if p.is_file() and ours(p, dest) and "@@" in p.read_text(errors="ignore")
    ]
    if leftovers:
        raise Failure(f"unresolved @@ tokens in {leftovers}")

    # 5. expected files present, forbidden files absent
    missing = [f for f in EXPECTED.get(preset, []) if not (dest / f).exists()]
    if missing:
        raise Failure(f"expected files missing: {missing}")
    present = [f for f in FORBIDDEN.get(preset, []) if (dest / f).exists()]
    if present:
        raise Failure(f"files that should have been excluded exist: {present}")

    # 6. no empty directories
    empties = [
        str(p.relative_to(dest))
        for p in dest.rglob("*")
        if p.is_dir() and ours(p, dest) and not any(p.iterdir())
    ]
    if empties:
        raise Failure(f"empty directories left behind: {empties}")

    # 7. re-apply must change nothing
    before = fingerprint(dest)
    proc = run(["apply", *common, "--dest", str(dest), "--json"])
    if proc.returncode != 0:
        raise Failure(f"re-apply failed: {proc.stdout.strip()[:400]}")
    after = fingerprint(dest)
    if after != before:
        changed = sorted(set(before) ^ set(after)) or [
            k for k in before if before[k] != after.get(k)
        ]
        raise Failure(f"re-apply changed {len(changed)} file(s): {changed[:5]}")

    # 8. the answers file is recorded, so the scaffold is reproducible
    if not (dest / ".project-setup-answers.yml").is_file():
        raise Failure("no answers file recorded")

    return {
        "layers": len(result["layers"]),
        "files": sum(1 for p in dest.rglob("*") if p.is_file() and ours(p, dest)),
        # Both are wall clock. Treat them as comparable only within one run:
        # they track machine load, not the scaffolder.
        "apply_seconds": round(result["seconds"], 2),
        "total_seconds": round(elapsed, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the output directory")
    parser.add_argument("--preset", help="run only this preset")
    args = parser.parse_args()

    if not CLI.is_file():
        print(f"FATAL: {CLI} not found. Run `just setup` first.", file=sys.stderr)
        return 2

    names = [args.preset] if args.preset else sorted(p.stem for p in PRESETS.glob("*.yml"))
    workdir = Path(tempfile.mkdtemp(prefix="project-setup-e2e-"))
    print(f"workdir: {workdir}\n")
    header = f"{'preset':<18}{'layers':>7}{'files':>7}{'apply':>8}{'total':>8}  result"
    print(header)
    print("-" * len(header))

    failures: list[tuple[str, str]] = []
    for preset in names:
        try:
            stats = check_preset(preset, workdir)
        except Failure as exc:
            failures.append((preset, str(exc)))
            print(f"{preset:<18}{'':>7}{'':>7}{'':>8}{'':>8}  FAIL")
        else:
            print(
                f"{preset:<18}{stats['layers']:>7}{stats['files']:>7}"
                f"{stats['apply_seconds']:>8}{stats['total_seconds']:>8}  ok"
            )
    print("-" * len(header))

    if failures:
        print(f"\n{len(failures)} preset(s) failed:\n")
        for preset, reason in failures:
            print(f"  {preset}: {reason}\n")
    else:
        print(
            f"\nall {len(names)} presets passed: "
            "validate, plan-writes-nothing, apply, no tokens, expected and forbidden "
            "files, no empty dirs, idempotent re-apply, answers recorded"
        )

    if args.keep:
        print(f"\noutput kept at {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())
