#!/usr/bin/env python3
"""Mutation audit: break a behavior, confirm the suite notices.

A test that asserts an empty result passes when the scan looked in the wrong place, and
a test that asserts a failure passes when the failure is not the one it names. Reading
them cannot settle either question. Breaking the subject can: if the suite still passes,
the test covering it is decorative.

Each mutation below is a one-line edit to a file the suite reads, paired with the tests
that claim to cover it. A mutation the suite does not catch is a finding.

    python3 tools/mutation_audit.py            every mutation
    python3 tools/mutation_audit.py --list     name them without running
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv/bin/python"


@dataclass
class Mutation:
    """One behavior, broken one way, and the tests that should object."""

    name: str
    path: str
    old: str
    new: str
    tests: str
    reported: bool = True  # False when the port refuses the mutation outright

    @property
    def file(self) -> Path:
        return REPO / self.path


MUTATIONS = [
    Mutation(
        name="member gets the root's forge and fragment surface",
        path="tools/port_assets.py",
        old="/.just.d/\n/.gitignore.d/\n/.pre-commit.d/\n",
        new="",
        tests="tests/test_toolchain.py -k member_exclude",
    ),
    Mutation(
        name="member keeps the root-walking lint packages",
        path="tools/tasks/native_init.py",
        old='{"@biomejs/biome", "oxlint", "oxlint-tsgolint", "knip"}',
        new="set()",
        tests="tests/test_toolchain.py -k root_walking",
    ),
    Mutation(
        name="an asked bool is a confirm again, so a keypress leaks",
        path="tools/port_assets.py",
        old='"choices": {\n        "Keep the pinned versions Renovate bumps": False,',
        new='"_choices": {\n        "Keep the pinned versions Renovate bumps": False,',
        tests="tests/test_interview.py -k select_not_a_confirm",
    ),
    Mutation(
        name="the ts layer ships no knip config",
        path="assets/lang/ts/knip.json",
        old='"entry": ["index.test.ts"],',
        new='"entry": [],',
        tests="tests/test_toolchain.py -k knip",
    ),
    Mutation(
        name="check runs the writing formatter again",
        path="assets/lang/ts/.just.d/ts.just",
        old="ts: ts-fmt-check",
        new="ts: ts-fmt",
        tests="tests/test_toolchain.py -k without_writing",
    ),
    Mutation(
        name="an empty a11y surface set fails instead of standing down",
        path="assets/a11y/ts/playwright/.just.d/a11y.just.template",
        old=" --pass-with-no-tests",
        new="",
        tests="tests/test_toolchain.py -k gap_not_a_failure",
    ),
    Mutation(
        name="a validator message repeats the name Copier already printed",
        path="tools/port_assets.py",
        old='f"must {rule}{{% endif %}}"',
        new='f"{name} must {rule}{{% endif %}}"',
        tests="tests/test_interview.py -k repeat_the_name",
    ),
    Mutation(
        name="a preset restates a pinned version",
        path="presets/parts/lang-go.yml",
        old="WANT_LANG_GO: true",
        new='WANT_LANG_GO: true\nGOLANGCI_LINT_VERSION: "2.7.1"',
        tests="tests/test_toolchain.py -k restates",
    ),
    Mutation(
        name="the CDK app is inside the root type-check again",
        path="assets/lang/ts/tsconfig.json.template",
        old=', "@@AWS_CDK_DEST@@"',
        new="",
        tests="tests/test_scaffold.py -k cdk",
    ),
    Mutation(
        name="a JSON comment goes back into biome.json",
        path="assets/lang/ts/biome.json.template",
        old='  "files": {',
        new='  // a comment biome will not forgive\n  "files": {',
        tests="tests/test_toolchain.py -k json_comment",
    ),
    Mutation(
        name="the CDK destination guard stops refusing an escape",
        path="assets/infrastructure/aws-cdk/scripts/init_aws_cdk.py",
        old="repository-relative path without dot segments",
        new="bad path",
        tests="tests/test_native_tools.py -k destination_stays_inside",
    ),
    Mutation(
        name="the gate applies a member without --member",
        path="tools/e2e.py",
        old='args = ["apply", "--member", "--dest", str(dest / member["path"])]',
        new='args = ["apply", "--dest", str(dest / member["path"])]',
        tests="tests/test_e2e_journey.py -k member_flag",
    ),
    Mutation(
        name="a placeholder is no longer reported after apply",
        path="src/project_setup/catalog.py",
        old='"PLACEHOLDER_IN_USE"',
        new='"PLACEHOLDER_UNREPORTED"',
        tests="tests/test_catalog.py -k placeholder",
    ),
    Mutation(
        name="a derived default masquerades as a literal value",
        path="src/project_setup/catalog.py",
        old='if isinstance(default, str) and "@@" in default:',
        new="if False:",
        tests="tests/test_catalog.py tests/test_interview.py -k derived",
    ),
    Mutation(
        name="a deselected layer's files are no longer reported",
        old='"STALE_LAYER_FILES"',
        path="src/project_setup/cli.py",
        new='"STALE_LAYER_QUIET"',
        tests="tests/test_scaffold.py -k stale",
    ),
    Mutation(
        name="an interrupted apply leaves no marker",
        path="src/project_setup/cli.py",
        old='"INTERRUPTED_APPLY"',
        new='"INTERRUPTED_QUIET"',
        tests="tests/test_interrupt.py",
    ),
    Mutation(
        name="the templates flag stops taking precedence over the env var",
        path="src/project_setup/cli.py",
        old='if explicit is not None:\n        candidates.append((f"--{kind}", explicit))',
        new='if False:\n        candidates.append((f"--{kind}", explicit))',
        tests="tests/test_resolve.py",
    ),
    Mutation(
        name="the forge exclusion stops swapping the CI surface",
        path="tools/port_assets.py",
        old="{% if FORGE_PLATFORM != 'github' %}\n/.github/\n{% endif %}",
        new="{% if False %}\n/.github/\n{% endif %}",
        tests="tests/test_scaffold.py -k gitlab",
    ),
    Mutation(
        name="members.json lands in every single-root project",
        path="tools/port_assets.py",
        old="{% if not IS_MONOREPO %}\n/.ci/members.json\n{% endif %}",
        new="{% if False %}\n/.ci/members.json\n{% endif %}",
        tests="tests/test_scaffold.py -k members_json",
    ),
    Mutation(
        name="a network retry stops being reported",
        path="tools/e2e.py",
        old='retried.append(f"{name} (succeeded on attempt {attempt})")',
        new="pass",
        tests="tests/test_e2e_journey.py -k transient",
    ),
    Mutation(
        name="a deterministic failure is retried like a transient one",
        path="tools/e2e.py",
        old="if attempt <= retries and NETWORK_TRANSIENT.search(output):",
        new="if attempt <= retries:",
        tests="tests/test_e2e_journey.py -k never_retried",
    ),
    Mutation(
        name="plan stops rehearsing what apply does",
        path="tools/e2e.py",
        old='("setup", "mise trust --yes --quiet && mise exec -- just setup"),',
        new='("setup", "true"),',
        tests="tests/test_e2e_journey.py",
    ),
    Mutation(
        name="a gate failure is printed but never written down",
        path="tools/e2e.py",
        old="        log.write_text(",
        new="        _ = (",
        tests="tests/test_e2e_journey.py -k truncated_read",
    ),
    Mutation(
        name="the failure log is no longer ignored, so a failed gate dirties the tree",
        path=".gitignore",
        old="e2e-failures.log",
        new="# e2e-failures.log",
        tests="tests/test_e2e_journey.py -k not_committed",
    ),
    Mutation(
        name="uv's placeholder description survives into the wheel",
        path="tools/tasks/native_init.py",
        old="text = text.replace(f'\"{UV_PLACEHOLDER_DESCRIPTION}\"', f'\"{escaped}\"', 1)",
        new="pass",
        tests="tests/test_native_tools.py -k answered_description",
    ),
    Mutation(
        name="the license line goes back to re.sub and its escaped backslashes",
        path="tools/tasks/native_init.py",
        old='text = f\'{text[: where.end()]}\\nlicense = "{spdx}"{text[where.end() :]}\'',
        new=(
            'text = re.sub(r"^(description\\s*=.*)$", '
            'rf"\\1\\nlicense = \\\\"{spdx}\\\\"", '
            "text, count=1, flags=re.MULTILINE)"
        ),
        tests="tests/test_native_tools.py -k valid_toml",
    ),
    Mutation(
        name="a license is claimed even when the project states none",
        path="tools/tasks/native_init.py",
        old='if spdx != NO_LICENSE and not re.search(r"^license\\s*=", text, re.MULTILINE)',
        new='if not re.search(r"^license\\s*=", text, re.MULTILINE)',
        tests="tests/test_native_tools.py -k states_none",
    ),
    Mutation(
        name="a brownfield description is overwritten",
        path="tools/tasks/native_init.py",
        old="if description and UV_PLACEHOLDER_DESCRIPTION in text:",
        new="if description:",
        tests="tests/test_native_tools.py -k brownfield_description",
    ),
    Mutation(
        name="the py task stops being handed the license and description",
        path="tools/port_assets.py",
        old=(
            "# split once: a description may carry a colon, an SPDX id may not.\n"
            '                "@@ SPDX_ID @@:@@ DESCRIPTION @@",'
        ),
        new=(
            "# split once: a description may carry a colon, an SPDX id may not.\n"
            '                "@@ SPDX_ID @@:",'
        ),
        tests="tests/test_native_tools.py -k python_task_is_given",
    ),
    Mutation(
        name="package.json goes back to stating no description or license",
        path="tools/tasks/native_init.py",
        old=(
            '    manifest.write_text(json.dumps(data, indent=2) + "\\n")\n'
            "    print(f\"native_init: set {', '.join(wrote)} in package.json\")"
        ),
        new="    pass",
        tests="tests/test_native_tools.py -k npm_manifest_states",
    ),
    Mutation(
        name="Cargo.toml goes back to being unpublishable",
        path="tools/tasks/native_init.py",
        old=(
            "    manifest.write_text("
            'body.replace(anchor, anchor + "\\n".join(additions) + "\\n", 1))'
        ),
        new="    pass",
        tests="tests/test_native_tools.py -k cargo_manifest_states",
    ),
    Mutation(
        name="the cargo fields are appended past the package table",
        path="tools/tasks/native_init.py",
        old=(
            "    manifest.write_text("
            'body.replace(anchor, anchor + "\\n".join(additions) + "\\n", 1))'
        ),
        new='    manifest.write_text(body + "\\n".join(additions) + "\\n")',
        tests="tests/test_native_tools.py -k inside_the_package_table",
    ),
    Mutation(
        name="a brownfield npm description is overwritten",
        path="tools/tasks/native_init.py",
        old='    if description and not data.get("description"):',
        new="    if description:",
        tests="tests/test_native_tools.py -k already_carries",
    ),
    Mutation(
        name="a broken package.json is rewritten instead of reported",
        path="tools/tasks/native_init.py",
        old=(
            '        print("native_init: WARNING package.json is not valid JSON, '
            "leaving it alone\")"
        ),
        new='        data = {}  # noqa',
        tests="tests/test_native_tools.py -k not_json",
    ),
    Mutation(
        name="the metadata slot splits on every colon, truncating the description",
        path="tools/tasks/native_init.py",
        old='    spdx, _, description = packed.partition(":")',
        new='    spdx, description = (packed.split(":") + [""])[:2]',
        tests="tests/test_native_tools.py -k first_colon_only",
    ),
    Mutation(
        name="the ts task stops being handed the license and description",
        path="tools/port_assets.py",
        old=(
            "# and no license while the governance layer had written LICENSE.\n"
            '                "@@ SPDX_ID @@:@@ DESCRIPTION @@",'
        ),
        new=(
            "# and no license while the governance layer had written LICENSE.\n"
            '                "@@ SPDX_ID @@:",'
        ),
        tests="tests/test_native_tools.py -k each_language_task",
    ),
    Mutation(
        name="the rust task stops being handed the license and description",
        path="tools/port_assets.py",
        old=(
            "# crate was unpublishable.\n"
            '                "@@ SPDX_ID @@:@@ DESCRIPTION @@",'
        ),
        new=(
            "# crate was unpublishable.\n"
            '                "@@ SPDX_ID @@:",'
        ),
        tests="tests/test_native_tools.py -k each_language_task",
    ),
    Mutation(
        name="a killed interview reports -9 instead of naming the harness timeout",
        path="tests/test_interview_pty.py",
        old="        if overran:\n            raise AssertionError(",
        new="        if False:\n            raise AssertionError(",
        tests="tests/test_interview_pty.py -k harness_timeout",
    ),
    Mutation(
        name="finish() raises even when the interview exited on its own",
        path="tests/test_interview_pty.py",
        old="        overran = self._process.poll() is None",
        new="        overran = True",
        tests="tests/test_interview_pty.py -k exits_badly",
    ),
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=False)


def port() -> subprocess.CompletedProcess:
    return run([str(PY), "tools/port_assets.py"])


def check(mutation: Mutation) -> tuple[str, str]:
    """Apply the mutation, run its tests, restore. Returns (verdict, detail)."""
    path = mutation.file
    if not path.is_file():
        return "ERROR", f"no such file: {mutation.path}"
    original = path.read_text()
    found = original.count(mutation.old)
    if found == 0:
        return "ERROR", f"anchor absent: {mutation.old[:52]!r}"
    if found > 1:
        # An ambiguous anchor mutates whichever copy comes first, which is not the one
        # the mutation names. That silently turned a caught mutation into a missed one
        # when a third language started passing the same argument, so it fails loudly
        # rather than reporting a result about the wrong code.
        return (
            "ERROR",
            f"anchor matches {found} places, so it is not specific: "
            f"{mutation.old[:40]!r}",
        )

    path.write_text(original.replace(mutation.old, mutation.new, 1))
    try:
        ported = port()
        if ported.returncode != 0:
            # The port refusing is itself a guard, and a legitimate way to be caught.
            return "caught", "the port refused the mutation"
        result = run([str(PY), "-m", "pytest", "-q", *mutation.tests.split()])
        if result.returncode != 0:
            first = next(
                (ln for ln in result.stdout.splitlines() if ln.startswith("FAILED")),
                "failed",
            )
            return "caught", first[:96]
        return "MISSED", "the suite passed with the behavior broken"
    finally:
        path.write_text(original)
        port()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for m in MUTATIONS:
            print(f"  {m.name}\n      {m.path} -> {m.tests}")
        return 0

    print(f"  {len(MUTATIONS)} mutations\n")
    missed: list[str] = []
    errors: list[str] = []
    for m in MUTATIONS:
        verdict, detail = check(m)
        print(f"  {verdict:<7} {m.name}")
        print(f"          {detail}")
        if verdict == "MISSED":
            missed.append(m.name)
        elif verdict == "ERROR":
            errors.append(f"{m.name}: {detail}")

    print()
    print(f"  caught {len(MUTATIONS) - len(missed) - len(errors)}, missed {len(missed)}, "
          f"could not run {len(errors)}")
    for name in missed:
        print(f"    MISSED: {name}")
    for err in errors:
        print(f"    ERROR:  {err}")
    return 1 if missed or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
