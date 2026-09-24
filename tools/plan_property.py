"""The property `plan` has to satisfy: what it reports is what `apply` then does.

`plan` is the only warning before files are written, and it lied twice -- listing the
justfile as both overwritten and merged, and promising "replaced outright" for a file
it would not touch -- both caught by hand. Spot checks catch the case somebody thought
of. This checks every file of every run: snapshot the destination, run `plan`, run
`apply`, snapshot again, and compare what `plan` said against the difference `apply`
actually made, for each of the dispositions a destination can be in.

Used by tools/e2e.py for every stack and by tests/test_plan_property.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from project_setup.runner import classify, snapshot

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / ".venv/bin/project-setup"

# A native tool writes these, so hand-written junk in them would only test the tool.
NATIVE_MANIFESTS = {
    "Cargo.toml",
    "Cargo.lock",
    "package.json",
    "bun.lock",
    "pyproject.toml",
    "uv.lock",
    "go.mod",
    "go.sum",
}
# Appended to a scaffold's own files to stand for a user editing it afterwards.
USER_EDIT = "# a line the user added\n"
EDITED = ("README.md", "justfile", ".gitignore", "AGENTS.md", ".github/workflows/ci.yml")


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(CLI), *args], capture_output=True, text=True, check=False, cwd=REPO)


def hand_written(relative: str) -> str:
    """Plausible content somebody could have written at this path by hand."""
    name = Path(relative).name
    if name == ".pre-commit-config.yaml":
        return (
            "repos:\n  - repo: local\n    hooks:\n      - id: mine\n        name: mine\n"
            "        entry: 'true'\n        language: system\n"
        )
    if name.endswith((".yml", ".yaml")):
        return f"hand: written  # {relative}\n"
    if name.endswith(".json"):
        return '{"hand": "written"}\n'
    if name.endswith(".toml"):
        return 'hand = "written"\n'
    if name == ".gitignore":
        return "build/\n*.log\n"
    if name == "justfile":
        return "hello:\n    echo hi\n"
    return f"hand-written {relative}\n"


def seed_brownfield(greenfield: Path, dest: Path) -> None:
    """A repository that already holds its own version of every file a scaffold writes."""
    for relative, entry in snapshot(greenfield).files.items():
        if isinstance(entry, str) or Path(relative).name in NATIVE_MANIFESTS:
            continue
        if relative.startswith(".project-setup"):
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(hand_written(relative))


def seed_edited(scaffolded: Path, dest: Path) -> None:
    """A scaffold its owner has since edited."""
    shutil.copytree(scaffolded, dest, symlinks=True)
    for relative in EDITED:
        path = dest / relative
        if path.is_file() and not path.is_symlink():
            with path.open("a") as handle:
                handle.write(USER_EDIT)


def mismatches(dest: Path, data_file: Path) -> list[str]:
    """Every way `plan` disagreed with the `apply` that followed it. Empty is honest."""
    common = ["--data-file", str(data_file), "--dest", str(dest)]
    before = snapshot(dest)
    plan = cli("plan", *common, "--json")
    if not plan.stdout.strip():
        return [f"plan printed nothing (exit {plan.returncode}): {plan.stderr.strip()[:300]}"]
    planned = json.loads(plan.stdout)
    if snapshot(dest) != before:
        return ["plan wrote into the destination"]
    applied_proc = cli("apply", *common, "--json")
    if not applied_proc.stdout.strip():
        return [
            f"apply printed nothing (exit {applied_proc.returncode}): {applied_proc.stderr[:300]}"
        ]
    applied = json.loads(applied_proc.stdout)
    actual = classify(before, snapshot(dest)).as_json()

    found: list[str] = []
    for key, value in actual.items():
        if planned["files"][key] != value:
            found.append(f"plan {key}: said {planned['files'][key]!r}, apply did {value!r}")
        if applied["files"][key] != value:
            found.append(f"apply reported {key} {applied['files'][key]!r}, did {value!r}")
    if planned["ok"] != applied["ok"]:
        found.append(f"plan said ok={planned['ok']}, apply ok={applied['ok']}")
    said = [w["message"] for w in planned["warnings"]]
    did = [w["message"] for w in applied["warnings"]]
    if said != did:
        found.append(f"plan warned {said!r}, apply warned {did!r}")
    return found


# Each disposition: its name, and how to build it from a finished greenfield scaffold.
DISPOSITIONS: tuple[tuple[str, Callable[[Path, Path], None] | None], ...] = (
    ("greenfield", None),
    ("re-apply", lambda green, dest: shutil.copytree(green, dest, symlinks=True)),
    ("edited", seed_edited),
    ("brownfield", seed_brownfield),
)


def check_stack(workdir: Path, data_file: Path) -> dict[str, list[str]]:
    """Run the property over every disposition of one answer set."""
    green = workdir / "greenfield"
    green.mkdir(parents=True)
    results = {"greenfield": mismatches(green, data_file)}
    for name, build in DISPOSITIONS[1:]:
        dest = workdir / name
        if build is seed_brownfield:
            dest.mkdir()
        build(green, dest)
        results[name] = mismatches(dest, data_file)
    return results
