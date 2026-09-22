"""End-to-end scaffold tests: the properties that make this safe to re-run.

Tasks are disabled so the suite stays offline and does not depend on bun or cargo.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from project_setup.catalog import load_catalog
from project_setup.runner import place_layers, prune_empty_dirs, run_generators

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
PRESETS = Path(__file__).resolve().parents[1] / "presets"

IDENTITY = {
    "PROJECT_NAME": "my-app",
    "DESCRIPTION": "A thing",
    "CODEOWNER": "@me",
    "SECURITY_CONTACT": "security@example.com",
}


def fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def scaffold(dest: Path, preset: str, extra: dict | None = None) -> object:
    catalog = load_catalog(TEMPLATES)
    data = {**(yaml.safe_load((PRESETS / f"{preset}.yml").read_text()) or {}), **IDENTITY}
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


def test_no_unresolved_tokens(scaffolded: Path):
    offenders = [
        str(p.relative_to(scaffolded))
        for p in scaffolded.rglob("*")
        if p.is_file() and ".git" not in p.parts and "@@" in p.read_text(errors="ignore")
    ]
    assert offenders == []


def test_applying_twice_changes_nothing(tmp_path: Path):
    assert scaffold(tmp_path, "polyglot-service").ok
    before = fingerprint(tmp_path)
    assert scaffold(tmp_path, "polyglot-service").ok
    assert fingerprint(tmp_path) == before


def test_overlapping_layers_fold_into_one_gitignore(scaffolded: Path):
    fragments = sorted(p.name for p in (scaffolded / ".gitignore.d").iterdir())
    assert fragments == ["go", "os", "ts"]
    body = (scaffolded / ".gitignore").read_text()
    assert "BEGIN PROJECT-SETUP MANAGED BLOCK" in body
    for probe in ("Icon", "vendor/", "node_modules"):
        assert probe in body, probe


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


def test_changing_a_merged_answer_is_refused_not_silently_applied(tmp_path: Path):
    """Documents a real constraint rather than hiding it.

    COMMIT_SCOPES feeds a hook definition that merge_hooks.py has already folded
    into .pre-commit-config.yaml. Re-applying with a different value is a semantic
    change to an existing hook, and the generator refuses it instead of
    overwriting. Recovery is to delete the generated file and re-run.
    """
    scaffold(tmp_path, "go-service")
    result = scaffold(tmp_path, "go-service", extra={"COMMIT_SCOPES": "totally,different"})

    conflicts = [s for s in result.generated if not s.ok]
    assert conflicts, "expected merge_hooks.py to refuse a changed hook definition"
    assert "conflict" in conflicts[0].detail

    # The documented recovery path.
    (tmp_path / ".pre-commit-config.yaml").unlink()
    recovered = scaffold(tmp_path, "go-service", extra={"COMMIT_SCOPES": "totally,different"})
    assert recovered.ok, [s.detail for s in recovered.generated if not s.ok]
    assert "totally,different" in (tmp_path / ".pre-commit-config.yaml").read_text()


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
    data = {**(yaml.safe_load((PRESETS / f"{preset}.yml").read_text()) or {}), **IDENTITY}
    data.update(extra or {})
    result = place_layers(catalog, dest, data, run_tasks=True, quiet=True)
    prune_empty_dirs(dest)
    if result.ok:
        run_generators(dest, result, data=data)
    return result


def test_licence_is_materialised_and_the_pool_removed(tmp_path: Path):
    result = scaffold_with_tasks(tmp_path, "minimal", extra={"SPDX_ID": "MPL-2.0"})
    assert result.ok, [s.detail for s in result.placed if not s.ok]
    assert "Mozilla Public License" in (tmp_path / "LICENSE").read_text()
    assert not (tmp_path / "licenses").exists(), "unused licence texts were left behind"


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
