#!/usr/bin/env python3
"""End-to-end check: drive the real CLI over every preset and verify the result.

    python3 tools/e2e.py [--keep] [--preset NAME]

Unlike the unit tests, this shells out to the installed `project-setup` command with
tasks enabled, so it covers the path a user or an agent actually takes: validate,
plan, apply, re-apply. Native toolchain init is not a question and is not suppressed:
each branch skips when its manifest exists and warns when its tool is absent, so the
run needs no toolchain but uses one when it is there.

After the scaffolder's own checks, each preset walks the journey a user takes next,
in the scaffolded repository, with the repository's own pinned toolchain active:

    mise exec -- just setup      install what the scaffold pins
    git add -A                   stage it, as the first commit will
    mise exec -- just check      the scaffold's own gate, over tracked files
    git commit                   the first commit, through every hook
    git status                   nothing left dirty or untracked

and, for a preset with the CDK layer, again after `just aws-cdk-init`. Every defect
the fourth round found came from running exactly this by hand, while this suite and
pytest passed throughout. Staging comes before `check` because `prek run --all-files`
reads tracked files only: in a repository with none, every hook reports "(no files to
check)" and `check` passes having checked nothing.

`mise exec` rather than whatever is on PATH, so the pins are what runs: a gate over
the developer's own newer toolchain passes for a set of versions nobody pinned. A
toolchain the scaffolder reports absent -- an apply warning "<tool> is not on PATH" --
skips the journey with that reason, and the summary counts every skip.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import plan_property
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


class Skipped(Exception):
    """The journey could not run here; the reason is reported, never counted as a pass."""


# The journey's environment: never prompt, never page. A commit hook or a mise trust
# prompt waiting on a terminal nobody is watching would hang the run instead of failing.
JOURNEY_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "SSH_ASKPASS": "/usr/bin/false",
    "SSH_ASKPASS_REQUIRE": "force",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "MISE_YES": "1",
    "GIT_AUTHOR_NAME": "e2e",
    "GIT_AUTHOR_EMAIL": "e2e@example.com",
    "GIT_COMMITTER_NAME": "e2e",
    "GIT_COMMITTER_EMAIL": "e2e@example.com",
}

# What the journey itself needs before any scaffold-specific toolchain.
JOURNEY_TOOLS = ("mise", "git")

ABSENT_TOOL = re.compile(r"(\S+) is not on PATH")

JOURNEY_TIMEOUT_SECONDS = 1800

# Where a failure's detail survives a truncated read of stdout.
FAILURE_LOG = "e2e-failures.log"

# `setup` fetches the toolchain and every language's dependencies, and `check` runs
# `prek run --all-files`, which on a fresh cache clones four hook repos over https
# (assets/hooks/.pre-commit.d/hygiene.yaml.template) before it runs anything local.
# `aws-cdk-init` downloads the CDK CLI through bunx and then `bun install`s the app
# it generates. All three are read-only towards the destination on failure -- a
# failed attempt leaves no partial state a retry would trip over -- so a transient
# network symptom in their own output is worth one retry rather than an immediate
# fail. `stage`, `commit`, and `clean` touch no network; a failure there is never
# retried, because retrying a deterministic failure only delays reporting it.
STEP_RETRIES: dict[str, int] = {"setup": 2, "check": 2, "aws-cdk-init": 2}

RETRY_BACKOFF_SECONDS = (10, 30)

# Matched against a failed step's own stdout+stderr. Each pattern was picked to name
# a transport-level symptom -- DNS, TCP, TLS, or a registry's own throttling reply --
# never a tool's verdict about the code, so a genuine lint or test failure repeats
# unchanged on every attempt and is never masked by this.
NETWORK_TRANSIENT = re.compile(
    r"could not resolve host"
    r"|temporary failure in name resolution"
    r"|name or service not known"
    r"|network is unreachable"
    r"|connection (?:reset|refused|timed out)"
    r"|(?:read|write|i/o|dial) tcp.*(?:timeout|refused|no such host)"
    r"|tls handshake timeout"
    r"|429 too many requests"
    r"|rate limit exceeded"
    r"|curl: \((?:6|7|28|35|56)\)"
    r"|fetch failed"
    r"|econnreset|enotfound|etimedout",
    re.IGNORECASE,
)

# After the first commit: nothing modified, nothing untracked. A hook that rewrites a
# file, or a setup step that writes one nobody ignores, both land here.
CLEAN_TREE = 'test -z "$(git status --porcelain)" || { git status --short; exit 1; }'


# What each native init writes, by the tool apply names when it is absent.
NATIVE_OUTPUT: dict[str, tuple[str, ...]] = {
    "cargo": ("Cargo.toml", "src/main.rs", "src/lib.rs"),
    "go": ("go.mod", "main.go"),
    "bun": ("package.json", "index.ts", "index.test.ts"),
    "uv": ("pyproject.toml",),
}


def absent_tools(warnings: list[dict]) -> set[str]:
    """The tools apply reported missing: native init warns "<tool> is not on PATH"."""
    return {m.group(1) for w in warnings if (m := ABSENT_TOOL.search(w.get("message", "")))}


def journey_skip_reason(warnings: list[dict], which=shutil.which) -> str | None:
    """Why the journey cannot run for this scaffold, or None when it can.

    A language toolchain is absent exactly when apply said so: native init warns
    "<tool> is not on PATH" and leaves the manifest unwritten, so `just setup` would fail
    on a missing Cargo.toml rather than on anything the scaffold got wrong.
    """
    missing = [tool for tool in JOURNEY_TOOLS if which(tool) is None]
    if missing:
        return f"{', '.join(missing)} not on PATH"
    absent = sorted(absent_tools(warnings))
    if absent:
        return f"apply reported {', '.join(absent)} not on PATH"
    return None


def journey_step(
    dest: Path,
    name: str,
    command: str,
    *,
    retries: int = 0,
    retried: list[str] | None = None,
) -> float:
    started = time.perf_counter()
    attempt = 0
    while True:
        attempt += 1
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                cwd=dest,
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, **JOURNEY_ENV},
                timeout=JOURNEY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise Failure(f"{name}: timed out after {JOURNEY_TIMEOUT_SECONDS}s") from exc
        if proc.returncode == 0:
            if attempt > 1 and retried is not None:
                retried.append(f"{name} (succeeded on attempt {attempt})")
            return time.perf_counter() - started
        output = (proc.stdout + proc.stderr).strip()
        # Retried only when the failure names a transport symptom, never a tool's
        # verdict about the code: a real lint or test failure repeats unchanged on
        # every attempt, so it costs a bounded wait, never a false pass.
        if attempt <= retries and NETWORK_TRANSIENT.search(output):
            time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
            continue
        lines = output.splitlines()
        # The first failing line names the cause; the last lines are just's own summary.
        cause = next((line for line in lines if re.search(r"Failed|error|FAIL", line)), "")
        shown = "\n      ".join(lines[-12:])
        note = f" after {attempt} attempts, still a transient symptom" if attempt > 1 else ""
        raise Failure(
            f"{name} exited {proc.returncode} in {dest}{note}\n"
            f"    first failure: {cause}\n"
            f"    last lines:\n      {shown}"
        )


def journey(dest: Path, *, label: str = "", retried: list[str] | None = None) -> dict[str, float]:
    """setup, stage, check, first commit, clean tree. Each must exit 0."""
    prefix = f"{label} " if label else ""
    steps = [
        ("setup", "mise trust --yes --quiet && mise exec -- just setup"),
        ("stage", "git add -A"),
        ("check", "mise exec -- just check"),
        ("commit", f"git commit -q --allow-empty -m 'chore: {label or 'initial'} scaffold'"),
        ("clean", CLEAN_TREE),
    ]
    return {
        name: journey_step(
            dest, prefix + name, command, retries=STEP_RETRIES.get(name, 0), retried=retried
        )
        for name, command in steps
    }


# Which language part scaffolds a member, from the capabilities its own entry declares.
# gen_caller.py reads the same file to build each member's CI job, so a member the gate
# cannot scaffold is a member CI cannot test either.
MEMBER_PART = {
    "go": "parts/lang-go",
    "python": "parts/lang-python",
    "rust": "parts/lang-rust",
    "ts": "parts/lang-ts",
}


def scaffold_members(dest: Path, retried: list[str]) -> int:
    """Scaffold every member `.ci/members.json` names, the way the owner's design says.

    The shell is scaffolded first, then project-setup runs again per member. Each member
    is applied with --member, which is what keeps a nested .git, a second LICENSE and the
    root's fragment directories out of it.
    """
    manifest = dest / ".ci/members.json"
    if not manifest.is_file():
        return 0
    members = json.loads(manifest.read_text()).get("members", [])
    for member in members:
        languages = sorted(member.get("capabilities", {}))
        parts = [MEMBER_PART[lang] for lang in languages if lang in MEMBER_PART]
        if not parts:
            raise Failure(f"member {member['name']!r} declares no language this gate can scaffold")
        args = ["apply", "--member", "--dest", str(dest / member["path"])]
        for part in parts:
            args += ["--preset", part]
        args += [
            "--set",
            f"PROJECT_NAME={member['name']}",
            "--set",
            f"DESCRIPTION=the {member['name']} member",
            "--json",
        ]
        proc = run(args)
        if proc.returncode != 0:
            raise Failure(
                f"member {member['name']!r} would not scaffold:\n"
                f"    {(proc.stdout + proc.stderr).strip()[:400]}"
            )
    return len(members)


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


def check_preset(preset: str, workdir: Path, run_journey: bool = True) -> dict:
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
    # A manifest a native init would have written is not expected when apply said that
    # init's tool is absent: that is the documented degradation, and the journey below
    # reports it as a skip rather than this check failing it as a missing file.
    absent = absent_tools(result.get("warnings", []))
    excused = {path for tool in absent for path in NATIVE_OUTPUT.get(tool, ())}
    missing = [f for f in EXPECTED.get(preset, []) if not (dest / f).exists() and f not in excused]
    if missing:
        raise Failure(f"expected files missing: {missing}")
    present = [f for f in FORBIDDEN.get(preset, []) if (dest / f).exists()]
    if present:
        raise Failure(f"files that should have been excluded exist: {present}")

    # 5b. nothing a build or an editor left behind. The suite imports
    # `templates/<layer>/scripts/*.py` by path, which writes a `__pycache__` beside
    # them, and Copier copied that bytecode into every scaffold until the layers
    # excluded it. The assertion is cheap and the failure was invisible.
    junk = [
        str(p.relative_to(dest))
        for p in dest.rglob("*")
        if ours(p, dest)
        and (p.name in ("__pycache__", ".DS_Store", ".pytest_cache", ".ruff_cache"))
    ]
    if junk:
        raise Failure(f"build artifacts placed into the scaffold: {junk}")

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

    # 9. plan is honest: for every disposition a destination can be in, what plan
    # reported is exactly the difference the apply after it made.
    found = {
        name: problems
        for name, problems in plan_property.check_stack(
            workdir / f"{preset}-plan", answer_file
        ).items()
        if problems
    }
    if found:
        shown = "; ".join(f"{name}: {problems[:3]}" for name, problems in found.items())
        raise Failure(f"plan disagreed with apply in {len(found)} disposition(s): {shown}")

    # 10. the user's journey, in the scaffold, with its own pins.
    journey_result = "off"
    retried: list[str] = []
    if run_journey:
        reason = journey_skip_reason(result.get("warnings", []))
        if reason:
            journey_result = f"skip: {reason}"
        else:
            journey(dest, retried=retried)
            journey_result = "ok"
            # The CDK layer's app is generated on demand, and `just check` failed on
            # fullstack-web the moment it existed: the formatter, the type-checker and
            # go's ./... all walked into it. So the journey runs again after it.
            if "infra-aws-cdk" in result["layers"]:
                journey_step(
                    dest,
                    "aws-cdk-init",
                    "mise exec -- just aws-cdk-init",
                    retries=STEP_RETRIES["aws-cdk-init"],
                    retried=retried,
                )
                journey(dest, label="aws-cdk-init", retried=retried)
                journey_result = "ok+cdk"
            # The owner's design for a monorepo: scaffold the shell, then run
            # project-setup per member. The root's own check has to survive them:
            # biome and oxlint each refused a member's nested config and broke it.
            count = scaffold_members(dest, retried)
            if count:
                journey(dest, label="members", retried=retried)
                journey_result = f"ok+{count} members"
    return {
        "journey": journey_result,
        "retried": retried,
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
    parser.add_argument(
        "--no-journey",
        action="store_true",
        help="skip setup/check/commit in the scaffold; reported, never a pass",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 2) // 2),
        help="presets to run at once; each runs in its own directory",
    )
    args = parser.parse_args()

    if not CLI.is_file():
        print(f"FATAL: {CLI} not found. Run `just setup` first.", file=sys.stderr)
        return 2

    names = [args.preset] if args.preset else sorted(p.stem for p in PRESETS.glob("*.yml"))
    workdir = Path(tempfile.mkdtemp(prefix="project-setup-e2e-"))
    print(f"workdir: {workdir}\n")
    header = f"{'preset':<18}{'layers':>7}{'files':>7}{'apply':>8}{'total':>8}  result  journey"
    print(header)
    print("-" * len(header))

    failures: list[tuple[str, str]] = []
    journeys: dict[str, str] = {}
    retried: dict[str, list[str]] = {}

    def one(preset: str) -> tuple[str, dict | None, str]:
        try:
            return preset, check_preset(preset, workdir, not args.no_journey), ""
        except Failure as exc:
            return preset, None, str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for preset, stats, reason in pool.map(one, names):
            if stats is None:
                failures.append((preset, reason))
                print(f"{preset:<18}{'':>7}{'':>7}{'':>8}{'':>8}  FAIL")
            else:
                print(
                    f"{preset:<18}{stats['layers']:>7}{stats['files']:>7}"
                    f"{stats['apply_seconds']:>8}{stats['total_seconds']:>8}  ok      "
                    f"{stats['journey']}"
                )
                journeys[preset] = stats["journey"]
                if stats["retried"]:
                    retried[preset] = stats["retried"]
    print("-" * len(header))

    if failures:
        # Written as well as printed. A reader who pipes this through `tail` sees the
        # count and loses the cause, which has now cost two sessions: the detail below
        # was on stdout both times and discarded before anyone read it.
        log = Path(FAILURE_LOG)
        log.write_text("\n\n".join(f"{preset}: {reason}" for preset, reason in failures) + "\n")
        print(f"\n{len(failures)} preset(s) failed (also written to {log}):\n")
        for preset, reason in failures:
            print(f"  {preset}: {reason}\n")
    else:
        print(
            f"\nall {len(names)} presets passed: "
            "validate, plan-writes-nothing, apply, no tokens, expected and forbidden "
            "files, no build artifacts, no empty dirs, idempotent re-apply, answers "
            "recorded, plan matches apply in 4 dispositions"
        )
    # Counted, never implied: a skipped journey is the gap a pass would hide.
    walked = sorted(p for p, j in journeys.items() if j.startswith("ok"))
    skipped = sorted((p, j) for p, j in journeys.items() if not j.startswith("ok"))
    print(
        f"\njourney (setup, check, first commit, clean tree): {len(walked)} walked, "
        f"{len(skipped)} not walked, {len(failures)} failed"
    )
    for preset, why in skipped:
        print(f"  {preset}: {why}")
    # A retry that succeeded is not a failure and not a skip, but it is still a
    # transient network symptom this run hit, so it is named rather than folded
    # silently into a plain "ok".
    if retried:
        print(f"\n{len(retried)} preset(s) needed a retry for a transient network symptom:")
        for preset, steps in sorted(retried.items()):
            for step in steps:
                print(f"  {preset}: {step}")

    if args.keep:
        print(f"\noutput kept at {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())
