"""End-to-end scaffold tests: the properties that make this safe to re-run.

Tasks are disabled so the suite stays offline and does not depend on bun or cargo.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

from project_setup.catalog import load_catalog
from project_setup.cli import load_preset
from project_setup.runner import (
    generator_args,
    place_layers,
    prune_empty_dirs,
    rehearse,
    run_generators,
    snapshot,
    split_operations,
)
from project_setup.runner import scaffold as runner_scaffold

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
PRESETS = Path(__file__).resolve().parents[1] / "presets"

IDENTITY = {"PROJECT_NAME": "my-app", "DESCRIPTION": "A thing"}


def fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def scaffold(dest: Path, preset: str, extra: dict | None = None) -> object:
    catalog = load_catalog(TEMPLATES)
    data = {**load_preset(preset, PRESETS)[0], **IDENTITY}
    data.update(extra or {})
    result = place_layers(catalog, dest, data, run_tasks=False, quiet=True)
    assert result.ok, [s.detail for s in result.placed if not s.ok]
    # Mirror the apply pipeline exactly, prune step included.
    prune_empty_dirs(dest)
    run_generators(dest, result, data=data)
    return result


@pytest.fixture
def scaffolded(tmp_path: Path) -> Path:
    result = scaffold(tmp_path, "polyglot-service")
    assert result.ok, [s.detail for s in result.generated if not s.ok]
    return tmp_path


def test_a_fresh_scaffold_passes_the_lint_gate_it_ships(tmp_path: Path):
    """The first commit ran ruff over scripts/ and failed on eleven findings.

    Those scripts are this repository's own plumbing, copied in, and the hook runs
    with --fix: the first commit attempt both failed and rewrote them in place. The
    scaffolder's own config is laxer than the one it ships, which is why nothing here
    noticed.
    """
    import subprocess

    scaffold(tmp_path, "py-lib")
    ruff = Path(sys.executable).parent / "ruff"
    for argv in (["check", "--no-cache"], ["format", "--check"]):
        done = subprocess.run(
            [str(ruff), *argv, "--config", str(tmp_path / "ruff.toml"), "scripts"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stdout + done.stderr


def test_no_unresolved_tokens(scaffolded: Path):
    offenders = [
        str(p.relative_to(scaffolded))
        for p in scaffolded.rglob("*")
        if p.is_file()
        and not {".git", "node_modules"} & set(p.parts)
        and "@@" in p.read_text(errors="ignore")
    ]
    assert offenders == []


def test_applying_twice_changes_nothing(tmp_path: Path):
    assert scaffold(tmp_path, "polyglot-service").ok
    before = fingerprint(tmp_path)
    assert scaffold(tmp_path, "polyglot-service").ok
    assert fingerprint(tmp_path) == before


def test_overlapping_layers_fold_into_one_gitignore(scaffolded: Path):
    fragments = sorted(p.name for p in (scaffolded / ".gitignore.d").iterdir())
    assert fragments == ["go", "os", "scripts", "ts"]
    body = (scaffolded / ".gitignore").read_text()
    assert "BEGIN PROJECT-SETUP MANAGED BLOCK" in body
    for probe in ("Icon", "vendor/", "node_modules"):
        assert probe in body, probe


def test_a_project_with_no_python_layer_still_ignores_its_own_bytecode(tmp_path: Path):
    """Every project gets `scripts/*.py`, and two hooks there import a sibling module.

    Importing writes bytecode beside the module, so the first commit in a fresh Rust
    repository left `scripts/__pycache__/` untracked and unignored, where `git add -A`
    picks it up. The Python layer ignores it; a project without that layer needs it
    just as much, so the base layer owns the fragment.
    """
    scaffold(tmp_path, "rust-cli")
    body = (tmp_path / ".gitignore").read_text()
    assert "__pycache__/" in body
    assert (tmp_path / "scripts/attribution_guard.py").is_file()


def test_overlapping_layers_merge_into_one_precommit_config(scaffolded: Path):
    cfg = yaml.safe_load((scaffolded / ".pre-commit-config.yaml").read_text())
    assert len(cfg["repos"]) >= 4
    assert set(cfg["default_install_hook_types"]) >= {"pre-commit", "commit-msg"}


def test_justfile_imports_every_fragment(scaffolded: Path):
    fragments = list((scaffolded / ".just.d").iterdir())
    body = (scaffolded / "justfile").read_text()
    block = body.split("BEGIN GENERATED: imports")[1].split("END GENERATED: imports")[0]
    assert block.count("import?") >= len(fragments)


def test_github_actions_expressions_survive_verbatim(scaffolded: Path):
    """The delimiter collision this whole design exists to avoid."""
    wf = scaffolded / ".github/workflows/wc-lint-ts.yml"
    assert "${{" in wf.read_text()
    source = TEMPLATES / "lang-ts/.github/workflows/wc-lint-ts.yml"
    assert wf.read_bytes() == source.read_bytes()


def test_justfile_interpolation_survives_in_fragments(scaffolded: Path):
    source = TEMPLATES / "lang-rust/.just.d/rust.just"
    if not source.is_file():
        pytest.skip("rust layer not selected in this preset")


def test_ci_caller_is_derived_from_the_placed_layers(scaffolded: Path):
    """gen_caller.py builds the workflow graph from the tree, not from a template."""
    caller = scaffolded / ".github/workflows/ci.yml"
    body = caller.read_text()
    for job in ("changes", "quality", "security", "gate"):
        assert f"{job}:" in body, job
    # polyglot-service selects go and ts, so both language jobs must appear
    assert "lint-go" in body and "lint-ts" in body


def test_steering_tree_is_remapped_and_indexed(scaffolded: Path):
    assert (scaffolded / "docs/agents/index.md").is_file()
    assert (scaffolded / "docs/agents/AGENTS.body.md").is_file()
    assert (scaffolded / "AGENTS.md").is_file()
    assert (scaffolded / "CLAUDE.md").is_symlink()


def test_forge_templates_are_remapped_to_dot_github(scaffolded: Path):
    assert (scaffolded / ".github/PULL_REQUEST_TEMPLATE.md").is_file()
    assert (scaffolded / ".github/ISSUE_TEMPLATE/bug_report.md").is_file()


def test_choosing_gitlab_swaps_the_whole_ci_surface(tmp_path: Path):
    result = scaffold(tmp_path, "gitlab-service")
    assert result.ok, [s.detail for s in result.generated if not s.ok]
    assert not (tmp_path / ".github").exists(), "GitHub content leaked into a GitLab project"
    assert (tmp_path / ".gitlab-ci.yml").is_file()
    assert (tmp_path / ".gitlab/ci/go.yml").is_file()
    assert (tmp_path / ".gitlab/issue_templates/bug.md").is_file()


def test_no_empty_directories_are_left_behind(tmp_path: Path):
    assert scaffold(tmp_path, "gitlab-service").ok
    empties = [
        str(p.relative_to(tmp_path))
        for p in tmp_path.rglob("*")
        if p.is_dir() and ".git" not in p.parts and not any(p.iterdir())
    ]
    assert empties == []


def test_members_json_only_exists_for_a_monorepo(tmp_path: Path):
    assert scaffold(tmp_path, "go-service").ok
    assert not (tmp_path / ".ci/members.json").exists()


def test_a_bare_answer_set_needs_no_preset_to_reach_the_generators(tmp_path: Path):
    """The two documented required answers really are the only two required.

    `--default-branch` is a generator argument assembled outside Copier, so an
    unanswered DEFAULT_BRANCH used to raise KeyError and abort a half-written
    scaffold. The layer default has to be resolved before the argument is filled.
    """
    catalog = load_catalog(TEMPLATES)
    result = place_layers(catalog, tmp_path, dict(IDENTITY), run_tasks=False, quiet=True)
    assert result.ok, [s.detail for s in result.placed if not s.ok]
    prune_empty_dirs(tmp_path)
    run_generators(tmp_path, result, data={**catalog.defaults_for(result.layers), **IDENTITY})
    assert result.ok, [s.detail for s in result.generated if not s.ok]
    assert "branches: [main]" in (tmp_path / ".github/workflows/ci.yml").read_text()


def test_a_missing_generator_argument_names_the_answer(tmp_path: Path):
    """A generator argument that cannot be filled says which answer is absent."""
    with pytest.raises(SystemExit) as raised:
        generator_args(("--default-branch", "{DEFAULT_BRANCH}"), {})
    assert "DEFAULT_BRANCH" in str(raised.value)


def rehearsed(dest: Path, preset: str, extra: dict | None = None):
    catalog = load_catalog(TEMPLATES)
    data = {**load_preset(preset, PRESETS)[0], **IDENTITY, **(extra or {})}
    return rehearse(catalog, dest, data)


def test_plan_names_every_file_it_would_overwrite(tmp_path: Path):
    """Copier overwrites by default, so the plan is the only warning a user gets."""
    (tmp_path / "README.md").write_text("hand written\n")
    (tmp_path / "justfile").write_text("build:\n    go build ./...\n")
    before = snapshot(tmp_path)

    result, changes = rehearsed(tmp_path, "go-service")

    assert result.ok, [s.detail for s in result.placed + result.generated if not s.ok]
    assert changes.overwrite == ["README.md"]
    assert changes.lost == {"README.md": 1}
    # The `just` layer skips an existing justfile and the generator appends its import
    # block, so every recipe survives. `plan` once listed it as overwritten *and*
    # merged; now it is whichever one the rehearsal measured.
    assert "justfile" in changes.merge
    assert "CODEOWNERS" in changes.create
    # A dry run that writes something is worse than no dry run at all.
    assert snapshot(tmp_path) == before


def test_a_greenfield_plan_counts_what_the_tasks_and_generators_create(tmp_path: Path):
    """A plan that skipped tasks and generators reported neither's files.

    It listed four `licenses/*.txt` to create that no apply ever left behind, and
    left out the LICENSE, .gitignore, ci.yml and AGENTS.md that every apply writes.
    """
    result, changes = rehearsed(tmp_path, "minimal")

    assert result.ok and result.pretend
    assert changes.overwrite == changes.merge == changes.remove == []
    for written in ("LICENSE", ".gitignore", ".github/workflows/ci.yml", "AGENTS.md", "CLAUDE.md"):
        assert written in changes.create
    assert not [p for p in changes.create if p.startswith("licenses/")]
    assert result.dest == tmp_path, "the report must name the destination, not the copy"
    assert list(tmp_path.iterdir()) == []


def test_bytecode_beside_a_layers_scripts_is_never_placed(tmp_path: Path):
    """`templates/` is written to after the port, by whatever runs there.

    This suite imports `templates/<layer>/scripts/*.py` by path, which leaves a
    `__pycache__` beside them. Copier then copied that bytecode into every scaffold: a
    fresh Rust repository carried a `.pyc` built by whichever interpreter last ran the
    tests, and `plan` listed it as a file to create. Poison the layer the way the suite
    does and assert nothing bytecode-shaped is placed.
    """
    poison = TEMPLATES / "steering" / "scripts" / "__pycache__"
    poison.mkdir(exist_ok=True)
    pyc = poison / "install_agents_index.cpython-313.pyc"
    pyc.write_bytes(b"\xcb\x0c\r\n")
    try:
        catalog = load_catalog(TEMPLATES)
        data = {**load_preset("minimal", PRESETS)[0], **IDENTITY}
        result = place_layers(catalog, tmp_path, data, pretend=True, run_tasks=False, quiet=True)
    finally:
        pyc.unlink()
        poison.rmdir()

    assert result.ok, [s.detail for s in result.placed if not s.ok]
    assert [p for p in result.files("create") if "__pycache__" in p or p.endswith(".pyc")] == []


def test_task_output_is_kept_out_of_the_file_operation_list():
    """A task's own message is the part worth echoing; file lines are worth counting."""
    files, rest = split_operations(
        "\nCopying from template version None\n"
        "\x1b[33m\x1b[1m overwrite\x1b[39m\x1b[0m  README.md\n"
        "\x1b[32m\x1b[1m    create\x1b[39m\x1b[0m  LICENSE\n"
        "materialise_license: wrote LICENSE from MIT\n"
    )
    assert files == {"overwrite": ["README.md"], "create": ["LICENSE"]}
    assert rest == "materialise_license: wrote LICENSE from MIT"


def test_monorepo_members_reach_the_manifest(tmp_path: Path):
    import json

    assert scaffold(tmp_path, "monorepo").ok
    manifest = json.loads((tmp_path / ".ci/members.json").read_text())
    names = sorted(m["name"] for m in manifest["members"])
    assert names == ["api", "web"]


def test_derived_python_version_needs_no_answer(tmp_path: Path):
    """PYTHON_VERSION_NODOT is computed from PYTHON_VERSION, not asked."""
    assert scaffold(tmp_path, "py-lib").ok
    assert 'target-version = "py313"' in (tmp_path / "ruff.toml").read_text()


def test_a_chosen_tool_version_reaches_the_files_that_pin_it(tmp_path: Path):
    """The point of asking at all: a user who needs 3.12 gets 3.12 everywhere.

    Including the derived PYTHON_VERSION_NODOT, which is computed from the answer
    rather than from the layer default.
    """
    assert scaffold(tmp_path, "py-lib", extra={"PYTHON_VERSION": "3.12"}).ok
    assert 'target-version = "py312"' in (tmp_path / "ruff.toml").read_text()
    assert '"3.12"' in (tmp_path / ".mise/conf.d/python.toml").read_text()


def test_api_contract_is_rendered_with_its_licence_url(tmp_path: Path):
    result = scaffold(
        tmp_path, "api-service", extra={"ORG": "example-org", "API_SERVER_URL": "https://api.x"}
    )
    assert result.ok
    contract = (tmp_path / "openapi.yaml").read_text()
    assert "https://api.x" in contract
    assert "spdx.org/licenses/Apache-2.0.html" in contract
    assert "@@" not in contract


def test_api_layer_adds_its_job_to_the_ci_caller(tmp_path: Path):
    scaffold(tmp_path, "api-service", extra={"ORG": "o", "API_SERVER_URL": "https://a"})
    assert "lint-api" in (tmp_path / ".github/workflows/ci.yml").read_text()


def test_i18n_project_lands_at_the_configured_path(tmp_path: Path):
    """A rendered path segment that is empty silently drops the file, so this is tested."""
    import json

    scaffold(
        tmp_path,
        "web-app",
        extra={"INLANG_MESSAGE_FORMAT_MODULE_URL": "https://cdn.example/plugin.js"},
    )
    settings = tmp_path / "project.inlang/settings.json"
    assert settings.is_file()
    assert json.loads(settings.read_text())["baseLocale"] == "en"


def test_i18n_project_dir_can_nest(tmp_path: Path):
    scaffold(
        tmp_path,
        "web-app",
        extra={
            "INLANG_MESSAGE_FORMAT_MODULE_URL": "https://cdn.example/plugin.js",
            "I18N_PROJECT_DIR": "apps/web/project.inlang",
        },
    )
    assert (tmp_path / "apps/web/project.inlang/settings.json").is_file()


def test_a11y_suite_is_an_isolated_package(tmp_path: Path):
    """It must work when the repository has no root package.json."""
    import json

    scaffold(
        tmp_path,
        "web-app",
        extra={"INLANG_MESSAGE_FORMAT_MODULE_URL": "https://cdn.example/plugin.js"},
    )
    pkg = tmp_path / ".a11y/package.json"
    assert pkg.is_file()
    json.loads(pkg.read_text())
    assert (tmp_path / ".a11y/playwright.config.ts").is_file()
    assert (tmp_path / ".a11y/tests/a11y.pw.ts").is_file()
    assert not (tmp_path / "package.json").exists()


def test_cdk_layer_ships_its_generator_not_a_generated_stack(tmp_path: Path):
    scaffold(
        tmp_path,
        "web-app",
        extra={"INLANG_MESSAGE_FORMAT_MODULE_URL": "https://cdn.example/plugin.js"},
    )
    assert (tmp_path / "scripts/init_aws_cdk.py").is_file()
    assert (tmp_path / ".just.d/aws-cdk.just").is_file()


def test_hand_written_work_survives_a_later_layer(tmp_path: Path):
    """The documented stage-2 path: same answers, one extra layer selected."""
    result = scaffold(tmp_path, "go-service")
    assert result.ok

    code = tmp_path / "main.go"
    code.write_text("package main\n\nfunc main() {}\n")
    digest = hashlib.sha256(code.read_bytes()).hexdigest()
    (tmp_path / ".gitignore").write_text(
        (tmp_path / ".gitignore").read_text() + "\n# hand written\nscratch/\n"
    )

    # Reuse the recorded answers and add only the selection flag, exactly as
    # SKILL.md instructs. Re-rendering with *different* answers is a separate case.
    later = scaffold(
        tmp_path,
        "go-service",
        extra={
            "WANT_LANG_TS": True,
            "NODE_VERSION": "24",
            "BUN_VERSION": "1.3.2",
            "BIOME_VERSION": "2.4.1",
        },
    )
    assert later.ok, [s.detail for s in later.generated if not s.ok]

    assert hashlib.sha256(code.read_bytes()).hexdigest() == digest
    assert "scratch/" in (tmp_path / ".gitignore").read_text()
    assert (tmp_path / "tsconfig.json").is_file()
    assert (tmp_path / ".golangci.yml").is_file()


def test_changing_a_merged_answer_re_derives_the_generated_file(tmp_path: Path):
    """COMMIT_SCOPES feeds a hook that is already folded into the generated config.

    The fragment is generated from the layer and the answer, so the layer owns that
    hook: a second apply with a new value has to land it. This used to be refused,
    which made the only recovery deleting the generated file by hand.
    """
    scaffold(tmp_path, "go-service")
    config = tmp_path / ".pre-commit-config.yaml"
    assert "api,ci,deps" in config.read_text()

    result = scaffold(tmp_path, "go-service", extra={"COMMIT_SCOPES": "totally,different"})

    assert result.ok, [s.detail for s in result.generated if not s.ok]
    assert "totally,different" in config.read_text()
    assert "api,ci,deps" not in config.read_text()


def test_declared_scopes_are_allowed_not_mandatory(tmp_path: Path):
    """COMMIT_SCOPES documents itself as the *allowed* scopes.

    `--force-scope` made one mandatory, so the first commit of a fresh scaffold --
    `feat: initial scaffold` -- was rejected with nothing but a link to
    conventionalcommits.org. Verified against the real hook afterwards: an unscoped
    subject is accepted, a declared scope is accepted, an undeclared one is refused.
    """
    scaffold(tmp_path, "py-lib")
    hooks = yaml.safe_load((tmp_path / ".pre-commit-config.yaml").read_text())
    args = next(
        hook["args"]
        for repo in hooks["repos"]
        for hook in repo["hooks"]
        if hook["id"] == "conventional-pre-commit"
    )
    assert "--force-scope" not in args
    assert args[args.index("--scopes") + 1] == "lib,docs,ci,deps"


def test_replacing_a_generated_entry_is_reported_not_silent(tmp_path: Path):
    scaffold(tmp_path, "go-service")
    result = scaffold(tmp_path, "go-service", extra={"COMMIT_SCOPES": "changed"})
    merge = next(s for s in result.generated if "merge_hooks" in s.name)
    assert "replaced" in merge.detail
    assert "conventional-pre-commit" in merge.detail


def test_a_brownfield_hook_config_survives_and_only_shared_ids_are_replaced(tmp_path: Path):
    """A repo with its own pre-commit config used to fail the whole apply.

    One shared hook id was enough: the existing definition and the fragment's
    differed, and the generator refused rather than deciding who owns it.
    """
    config = tmp_path / ".pre-commit-config.yaml"
    config.write_text(
        "repos:\n"
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.5.0\n"
        "    hooks:\n"
        "      - id: ruff\n"
        "  - repo: builtin\n"
        "    hooks:\n"
        "      - id: trailing-whitespace\n"
        '        args: ["--mine"]\n'
    )

    result = scaffold(tmp_path, "go-service")
    assert result.ok, [s.detail for s in result.generated if not s.ok]

    merged = yaml.safe_load(config.read_text())
    entries = {entry["repo"]: entry for entry in merged["repos"]}

    # No fragment declares ruff-pre-commit, so it is the repository's own and is kept.
    assert entries["https://github.com/astral-sh/ruff-pre-commit"]["rev"] == "v0.5.0"
    # A fragment does declare trailing-whitespace, so the layer's definition wins.
    builtin = {hook["id"]: hook for hook in entries["builtin"]["hooks"]}
    assert "--mine" not in str(builtin["trailing-whitespace"])


def test_two_fragments_disagreeing_is_still_a_hard_error(tmp_path: Path):
    """A template bug, which no answer can resolve, stays a refusal."""
    scaffold(tmp_path, "go-service")
    fragment = tmp_path / ".pre-commit.d/zz-conflicting.yaml"
    fragment.write_text(
        "repos:\n"
        "  - repo: https://github.com/crate-ci/typos\n"
        "    rev: v0.0.0-not-the-pinned-one\n"
        "    hooks:\n"
        "      - id: typos\n"
    )
    result = run_generators(
        tmp_path,
        place_layers(
            load_catalog(TEMPLATES),
            tmp_path,
            {**load_preset("go-service", PRESETS)[0], **IDENTITY},
            run_tasks=False,
        ),
        data={**load_preset("go-service", PRESETS)[0], **IDENTITY},
    )
    merge = next(s for s in result.generated if "merge_hooks" in s.name)
    assert not merge.ok
    assert "conflict" in merge.detail and "pinned" in merge.detail


def test_a_repositorys_own_justfile_survives_and_gains_the_imports(tmp_path: Path):
    """`plan` said "merged, your entries kept" about a file the layer overwrote.

    The `just` layer used to place its justfile unconditionally, so a brownfield
    repository's recipes were gone and the only warning it got claimed the opposite.
    The layer's own justfile carries no answer, so skipping an existing one re-derives
    nothing; the generator still owns the import block wherever the file came from.
    """
    (tmp_path / "justfile").write_text("test:\n    pytest -q\n\nrelease:\n    ./release.sh\n")

    result = scaffold(tmp_path, "py-lib")

    body = (tmp_path / "justfile").read_text()
    assert "pytest -q" in body, "the repository's own recipe was replaced"
    assert "./release.sh" in body
    assert "import? '.just.d/python.just'" in body
    assert "justfile" not in result.files("overwrite")
    gen = next(s for s in result.generated if "gen_justfile" in s.name)
    assert gen.ok and "appended" in gen.detail


# --------------------------------------------------------------------------- tasks
#
# These run with tasks enabled, which is how a real apply works. They stay offline:
# git_init, materialise_license and write_adrs touch nothing but the filesystem.

ADRS = """[
  {"title": "Use Copier", "decision": "Render layered templates.",
   "rationale": "Determinism belongs in the engine.", "consequences": "No update path."},
  {"title": "Pin tool versions", "decision": "Pin and let Renovate bump.",
   "rationale": "Reproducible offline.", "consequences": "Versions lag."}
]"""


def scaffold_with_tasks(dest: Path, preset: str, extra: dict | None = None) -> object:
    catalog = load_catalog(TEMPLATES)
    data = {**load_preset(preset, PRESETS)[0], **IDENTITY}
    data.update(extra or {})
    result = place_layers(catalog, dest, data, run_tasks=True, quiet=True)
    prune_empty_dirs(dest)
    if result.ok:
        run_generators(dest, result, data=data)
    return result


def test_a_degraded_task_is_reported_not_swallowed(tmp_path: Path, monkeypatch):
    """`apply` reported a clean Rust scaffold with no Cargo.toml in it.

    `cargo` was not on PATH, the task skipped as designed, and the skip went into a
    captured log nothing printed: `place ok lang-rust`, `95 file(s) created`, exit 0.
    Degrading is right -- a missing toolchain must not fail a scaffold -- so the run
    still succeeds and the warning is what carries the missing manifest.
    """
    # Copier runs tasks through plumbum, which snapshots the environment when it is
    # imported -- during collection, before any monkeypatch. So `setenv` alone changes
    # nothing the task can see, and the scaffold gets a real cargo.
    from plumbum import local

    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setitem(local.env, "PATH", "/nonexistent")

    result = scaffold_with_tasks(tmp_path, "rust-cli")

    assert result.ok, "a missing toolchain must not fail the scaffold"
    assert not (tmp_path / "Cargo.toml").exists()
    messages = [message for _, message in result.warnings]
    assert any("cargo" in m and "Cargo.toml" in m for m in messages), messages
    assert any("git" in m for m in messages), messages


def test_licence_is_materialised_and_the_pool_removed(tmp_path: Path):
    result = scaffold_with_tasks(tmp_path, "minimal", extra={"SPDX_ID": "MPL-2.0"})
    assert result.ok, [s.detail for s in result.placed if not s.ok]
    assert "Mozilla Public License" in (tmp_path / "LICENSE").read_text()
    assert not (tmp_path / "licenses").exists(), "unused licence texts were left behind"


def test_a_project_can_state_no_licence_at_all(tmp_path: Path):
    """Four choices were four ways to publish.

    An internal service, a work repository or a private tool is published under none of
    them, and `SPDX_ID` refused anything else -- so a private repository got an
    Apache-2.0 LICENSE it never chose, which is a statement about the code rather than
    an inconvenience.
    """
    result = scaffold_with_tasks(tmp_path, "minimal", extra={"SPDX_ID": "NONE"})

    assert result.ok, [s.detail for s in result.placed if not s.ok]
    assert not (tmp_path / "LICENSE").exists()
    assert not (tmp_path / "licenses").exists(), "unused licence texts were left behind"
    # A deliberate answer, so it is not a degradation.
    assert result.warnings == []


def test_an_unlicensed_rust_crate_passes_its_own_licence_gate(tmp_path: Path):
    """`cargo deny check licenses` fails a crate it reads as unlicensed.

    `cargo init` writes no `license` field, so cargo-deny falls back to the LICENSE
    file -- which SPDX_ID=NONE does not write. Measured with cargo-deny 0.19:
    `error[unlicensed]: <name> is unlicensed` unless the manifest marks the crate
    unpublishable, which is what makes deny.toml's `[licenses.private]` block apply.
    """
    result = scaffold_with_tasks(tmp_path, "rust-cli", extra={"SPDX_ID": "NONE"})

    assert result.ok, [s.detail for s in result.placed if not s.ok]
    deny = (tmp_path / "deny.toml").read_text()
    assert "[licenses.private]" in deny
    assert "NONE" not in deny, "NONE is not a licence a dependency can carry"
    manifest = tmp_path / "Cargo.toml"
    if manifest.is_file():  # skipped when cargo is not installed
        assert "publish = false" in manifest.read_text()


def test_a_licensed_rust_crate_is_still_publishable(tmp_path: Path):
    scaffold_with_tasks(tmp_path, "rust-cli", extra={"SPDX_ID": "MIT"})
    deny = (tmp_path / "deny.toml").read_text()
    assert "MIT" in deny
    assert "[licenses.private]" not in deny
    manifest = tmp_path / "Cargo.toml"
    if manifest.is_file():
        assert "publish" not in manifest.read_text()


def test_git_is_initialised_once(tmp_path: Path):
    assert scaffold_with_tasks(tmp_path, "minimal").ok
    assert (tmp_path / ".git").is_dir()
    head = (tmp_path / ".git/HEAD").read_bytes()
    assert scaffold_with_tasks(tmp_path, "minimal").ok
    assert (tmp_path / ".git/HEAD").read_bytes() == head


def test_one_file_is_written_per_adr(tmp_path: Path):
    assert scaffold_with_tasks(tmp_path, "minimal", extra={"ADRS": ADRS}).ok
    names = sorted(p.name for p in (tmp_path / "docs/adr").glob("*.md"))
    assert names == ["0001-use-copier.md", "0002-pin-tool-versions.md"]
    body = (tmp_path / "docs/adr/0001-use-copier.md").read_text()
    assert "# Use Copier" in body
    assert "Status: accepted" in body
    assert "None recorded." in body  # alternatives defaulted
    assert "@@" not in body


def test_reapplying_adrs_writes_nothing_new(tmp_path: Path):
    scaffold_with_tasks(tmp_path, "minimal", extra={"ADRS": ADRS})
    before = sorted(p.name for p in (tmp_path / "docs/adr").glob("*.md"))
    scaffold_with_tasks(tmp_path, "minimal", extra={"ADRS": ADRS})
    assert sorted(p.name for p in (tmp_path / "docs/adr").glob("*.md")) == before


def test_a_new_adr_continues_the_numbering(tmp_path: Path):
    scaffold_with_tasks(tmp_path, "minimal", extra={"ADRS": ADRS})
    more = (
        ADRS[:-2]
        + """,
  {"title": "Adopt mise", "decision": "Pin toolchains with mise.",
   "rationale": "One manager across languages.", "consequences": "Contributors need mise."}
]"""
    )
    scaffold_with_tasks(tmp_path, "minimal", extra={"ADRS": more})
    assert (tmp_path / "docs/adr/0003-adopt-mise.md").is_file()


def test_an_adr_without_a_decision_is_refused(tmp_path: Path):
    result = scaffold_with_tasks(
        tmp_path, "minimal", extra={"ADRS": '[{"title": "No decision recorded"}]'}
    )
    assert not result.ok
    detail = " ".join(s.detail for s in result.placed)
    assert "missing" in detail and "decision" in detail


def test_a_hand_written_ci_caller_is_not_reported_as_changing(tmp_path):
    """`plan` threatened a replacement that never happens.

    `gen_caller.py` refuses a ci.yml it did not write, and the plan said "replaced
    outright" about it -- the one line that would make somebody move the file first.
    The rehearsal measures what the generator does, so a file it leaves alone is on
    no list, and its own warning says why.
    """
    caller = tmp_path / ".github/workflows/ci.yml"
    caller.parent.mkdir(parents=True)
    caller.write_text("name: ci\non: [push]\n")

    result, changes = rehearsed(tmp_path, "minimal")

    assert ".github/workflows/ci.yml" not in changes.overwrite + changes.merge
    assert any("hand-owned" in message for _, message in result.warnings)


def test_a_plan_says_the_apply_would_fail_before_it_does(tmp_path):
    """A .pre-commit-config.yaml merge_hooks cannot read failed the apply, and the
    plan beforehand had promised "merged, your entries kept"."""
    (tmp_path / ".pre-commit-config.yaml").write_text("just a string\n")

    result, _changes = rehearsed(tmp_path, "minimal")

    assert not result.ok
    failed = [s for s in result.generated if not s.ok]
    assert [s.name for s in failed] == ["scripts/merge_hooks.py"]
    assert (tmp_path / ".pre-commit-config.yaml").read_text() == "just a string\n"


def test_a_hand_written_claude_md_is_reported_as_merged_into_the_link(tmp_path):
    """Its text is folded into AGENTS.md and CLAUDE.md becomes a link to it, which
    loses nothing; the plan used to call that "replaced outright"."""
    (tmp_path / "CLAUDE.md").write_text("Use tabs.\n")

    _result, changes = rehearsed(tmp_path, "minimal")

    assert "CLAUDE.md" in changes.merge
    assert changes.links == {"CLAUDE.md": "AGENTS.md"}


def test_a_link_out_of_the_destination_is_never_written_through(tmp_path):
    """The rehearsal copies the destination; a link out of it would carry a step's
    writes back into the real world, which a dry run must never do."""
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("shared by several repositories\n")
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "README.md").symlink_to(elsewhere)

    _result, changes = rehearsed(dest, "minimal")

    assert elsewhere.read_text() == "shared by several repositories\n"
    assert (dest / "README.md").is_symlink()
    assert "README.md" in changes.overwrite


def test_classify_separates_what_is_lost_from_what_is_kept():
    from project_setup.runner import Snapshot, classify

    before = Snapshot(
        {"kept": b"a\nb\n", "lost": b"a\nb\n", "gone": b"x\n", "same": b"s\n", "blank": b"\n"},
        frozenset(),
    )
    after = Snapshot(
        {"kept": b"a\nnew\nb\n", "lost": b"a\n", "same": b"s\n", "new": b"n\n", "blank": b"z\n"},
        frozenset(),
    )
    changes = classify(before, after)
    assert changes.create == ["new"]
    assert changes.remove == ["gone"]
    assert changes.overwrite == ["lost"] and changes.lost == {"lost": 1}
    # A blank line is not content somebody loses.
    assert changes.merge == ["blank", "kept"]


def test_a_repositorys_own_empty_directories_survive(tmp_path: Path):
    """Prune removed every empty directory in the destination, not only the ones the
    run had created: a brownfield `logs/`, and empty directories inside node_modules."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "node_modules/pkg/empty").mkdir(parents=True)
    catalog = load_catalog(TEMPLATES)
    data = {**load_preset("minimal", PRESETS)[0], **IDENTITY}

    result = runner_scaffold(
        catalog, tmp_path, data, keep_dirs=snapshot(tmp_path).dirs, run_tasks=False
    )

    assert result.ok
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "node_modules/pkg/empty").is_dir()
    assert not (tmp_path / ".gitlab").exists(), "a directory the run left empty still goes"


def test_a_reuse_licenses_directory_survives(tmp_path: Path):
    """The licence pool was placed as `licenses/` and then deleted, and on a
    case-insensitive filesystem that took a REUSE `LICENSES/` directory with it."""
    (tmp_path / "LICENSES").mkdir()
    (tmp_path / "LICENSES/CC0-1.0.txt").write_text("CC0\n")

    result = scaffold_with_tasks(tmp_path, "minimal", extra={"SPDX_ID": "MIT"})

    assert result.ok, [s.detail for s in result.placed if not s.ok]
    assert (tmp_path / "LICENSES/CC0-1.0.txt").read_text() == "CC0\n"
    assert "MIT License" in (tmp_path / "LICENSE").read_text()


def test_deselecting_a_layer_names_the_files_it_leaves_behind(tmp_path):
    """Copier excludes what a layer no longer contributes; it deletes nothing.

    So turning a layer off left its whole output in place, exactly as switching
    FORGE_PLATFORM did. The destination's own answers file is the record of what was
    selected last time, so no manifest is needed to notice.
    """
    from project_setup.catalog import ANSWERS_FILE, load_catalog
    from project_setup.runner import deselected_layers

    catalog = load_catalog(TEMPLATES)
    (tmp_path / ANSWERS_FILE).write_text("WANT_RELEASE: true\nWANT_LANG_GO: true\n")

    assert deselected_layers(catalog, tmp_path, {"WANT_LANG_GO": True}) == ["release"]
    # Nothing dropped when the selection is unchanged, or grew.
    assert deselected_layers(catalog, tmp_path, {"WANT_RELEASE": True, "WANT_LANG_GO": True}) == []
    assert deselected_layers(catalog, tmp_path, {"LAYERS": ["release", "lang-go", "api"]}) == []


def test_a_destination_with_no_answers_file_has_nothing_deselected(tmp_path):
    """A layer that was never applied is absent, not deselected."""
    from project_setup.catalog import load_catalog
    from project_setup.runner import deselected_layers

    catalog = load_catalog(TEMPLATES)
    assert deselected_layers(catalog, tmp_path, {}) == []


def test_the_orphan_scan_names_only_files_that_are_actually_there(tmp_path):
    """The list comes from a pretend place, because a layer's paths are templated.

    A static walk of the layer directory would report paths Copier excluded and miss
    the ones it expands, so the only list right by construction is Copier's own.
    """
    from project_setup.catalog import load_catalog
    from project_setup.runner import orphaned_files

    catalog = load_catalog(TEMPLATES)
    assert orphaned_files(catalog, tmp_path, ["release"]) == {}

    (tmp_path / "release-please-config.json").write_text("{}\n")
    found = orphaned_files(catalog, tmp_path, ["release"])

    assert "release-please-config.json" in found["release"]
    assert all((tmp_path / p).exists() for p in found["release"])


def test_a_deselected_layers_leftovers_are_a_warning_not_a_refusal(tmp_path):
    """The user may be one `git rm` from meaning it; deleting their files is not ours."""
    from project_setup.catalog import ANSWERS_FILE, load_catalog
    from project_setup.cli import checkout_problems

    catalog = load_catalog(TEMPLATES)
    (tmp_path / ANSWERS_FILE).write_text("WANT_RELEASE: true\n")
    (tmp_path / "release-please-config.json").write_text("{}\n")

    problems = checkout_problems(catalog, tmp_path, {"PROJECT_NAME": "x"})
    stale = [p for p in problems if p.code == "STALE_LAYER_FILES"]

    assert len(stale) == 1
    assert stale[0].level == "warning"
    assert stale[0].key == "WANT_RELEASE"
    assert "release-please-config.json" in stale[0].message


def test_a_greenfield_destination_costs_no_orphan_scan(tmp_path, monkeypatch):
    """The scan dry-runs Copier per dropped layer, so it must not run by default."""
    from project_setup import cli
    from project_setup.catalog import load_catalog

    catalog = load_catalog(TEMPLATES)

    def fail(*args, **kwargs):
        raise AssertionError("orphaned_files ran for a destination with nothing deselected")

    monkeypatch.setattr(cli, "orphaned_files", fail)

    assert cli.checkout_problems(catalog, tmp_path, {"PROJECT_NAME": "x"}) == []


def test_the_root_typecheck_excludes_the_cdk_project(tmp_path: Path):
    """TypeScript's default include is `**/*`, and a nested tsconfig does not shield
    its own subtree. Without this exclusion the CDK app's jest tests are type-checked
    by the root config, which has `types: ["bun"]`, so `just check` fails with TS2593
    on a scaffold the user has not touched. Dot-prefixed trees like .a11y escape
    because `**/*` does not match them.
    """
    assert scaffold(tmp_path, "fullstack-web").ok
    exclude = json.loads(re.sub(r"//.*", "", (tmp_path / "tsconfig.json").read_text()))["exclude"]
    assert "infrastructure" in exclude


def test_the_root_typecheck_follows_a_custom_cdk_destination(tmp_path: Path):
    assert scaffold(tmp_path, "fullstack-web", extra={"AWS_CDK_DEST": "deploy/cdk"}).ok
    exclude = json.loads(re.sub(r"//.*", "", (tmp_path / "tsconfig.json").read_text()))["exclude"]
    assert "deploy/cdk" in exclude
    assert "infrastructure" not in exclude


def test_the_shell_form_of_the_cdk_path_is_derived_not_answered(tmp_path: Path):
    """One source of truth: shell-quoting a value and then embedding it in JSON
    produces invalid JSON the moment a path contains a space."""
    from project_setup.catalog import load_catalog

    catalog = load_catalog(TEMPLATES)
    questions = catalog.questions_for(["infra-aws-cdk"])
    assert questions["AWS_CDK_DEST_SHELL"].derived
    assert "AWS_CDK_DEST" in questions["AWS_CDK_DEST_SHELL"].derived_from
    assert not questions["AWS_CDK_DEST"].derived
