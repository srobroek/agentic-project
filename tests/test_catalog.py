"""Tests for the catalog contract: selection, validation, and the question set.

These are the guarantees an agent depends on, so they are tested rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_setup.catalog import (
    ALWAYS_ON,
    layer_of,
    load_catalog,
    selected_layers,
    validate_data,
    want_var,
)

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
PRESETS = Path(__file__).resolve().parents[1] / "presets"

IDENTITY = {"PROJECT_NAME": "my-app", "DESCRIPTION": "A thing"}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(TEMPLATES)


def test_want_var_roundtrip():
    assert want_var("lang-go") == "WANT_LANG_GO"
    assert layer_of("WANT_LANG_GO") == "lang-go"


def test_every_layer_declares_a_config(catalog):
    assert set(ALWAYS_ON) <= set(catalog.layers)
    for layer in catalog.layers.values():
        assert (layer.path / "copier.yml").is_file()


def test_always_on_layers_need_no_selection(catalog):
    layers = selected_layers(catalog, {})
    assert layers == [n for n in ALWAYS_ON if n in catalog.layers]


def test_optional_layer_is_selected_by_its_want_flag(catalog):
    layers = selected_layers(catalog, {"WANT_LANG_GO": True})
    assert "lang-go" in layers
    assert "lang-ts" not in layers


def test_false_want_flag_does_not_select(catalog):
    assert "lang-go" not in selected_layers(catalog, {"WANT_LANG_GO": False})


def test_apply_order_puts_always_on_first(catalog):
    order = catalog.apply_order()
    last_always = max(order.index(n) for n in ALWAYS_ON if n in order)
    first_optional = min(order.index(n) for n in catalog.optional_layers())
    assert last_always < first_optional


def test_bare_answers_report_exactly_the_identity_gaps(catalog):
    problems = validate_data(catalog, {})
    missing = {p.key for p in problems if p.code == "MISSING_REQUIRED"}
    assert missing == set(IDENTITY)


def test_a_missing_answer_names_the_flag_that_supplies_it(catalog):
    """It read "required by layer(s) base and has no default. One-line purpose".

    The help ran onto the end of a sentence, and the one thing the reader needs -- how
    to supply the value -- was not in it. This is the first error most users meet.
    """
    message = next(p.message for p in validate_data(catalog, {}) if p.key == "DESCRIPTION")
    assert "--set DESCRIPTION=" in message
    assert message.startswith("One-line purpose")


def test_turning_off_an_always_on_layer_says_why_it_cannot(catalog):
    """`WANT_CI: false` is neither a typo nor an unselected layer.

    It read "no layer declares this; it will be ignored. Typo, or a layer you did not
    select?", which sends somebody looking for a spelling mistake in the name of a
    layer that exists and is applied to every project.
    """
    problems = validate_data(catalog, {**IDENTITY, "WANT_CI": False})
    named = [p for p in problems if p.key == "WANT_CI"]

    assert [p.code for p in named] == ["ALWAYS_ON_LAYER"]
    assert "every project" in named[0].message
    assert named[0].level == "warning"


def test_identity_answers_satisfy_the_always_on_layers(catalog):
    problems = [p for p in validate_data(catalog, IDENTITY) if p.level == "error"]
    assert problems == []


def test_invalid_choice_is_an_error(catalog):
    problems = validate_data(catalog, {**IDENTITY, "SPDX_ID": "WTFPL"})
    assert any(p.code == "INVALID_CHOICE" and p.key == "SPDX_ID" for p in problems)


def test_unknown_key_is_a_warning_not_an_error(catalog):
    problems = validate_data(catalog, {**IDENTITY, "NOPE": 1})
    unknown = [p for p in problems if p.code == "UNKNOWN_KEY"]
    assert len(unknown) == 1
    assert unknown[0].level == "warning"


def test_selecting_a_layer_adds_its_questions(catalog):
    without = set(catalog.questions_for(selected_layers(catalog, {})))
    with_go = set(catalog.questions_for(selected_layers(catalog, {"WANT_LANG_GO": True})))
    assert "GO_VERSION" in with_go - without


# No preset should need anything beyond the two identity answers. Anything a user
# cannot know at setup time is a placeholder, not a blocker, so this stays empty.
DOCUMENTED_GAPS: dict[str, set[str]] = {}


@pytest.mark.parametrize("preset", sorted(p.stem for p in PRESETS.glob("*.yml")))
def test_a_preset_leaves_only_identity_and_documented_gaps(preset, catalog):
    """A preset must define a complete shape. Anything it cannot answer is declared."""
    from project_setup.cli import load_preset

    data = load_preset(preset, PRESETS)[0]
    errors = [p for p in validate_data(catalog, {**data, **IDENTITY}) if p.level == "error"]
    gaps = {p.key for p in errors}
    assert gaps <= DOCUMENTED_GAPS.get(preset, set()), (
        f"{preset} has undeclared gaps: {sorted(gaps - DOCUMENTED_GAPS.get(preset, set()))}"
    )


def test_every_layer_is_reachable_from_some_preset(catalog):
    """A layer no preset selects is a layer nobody will discover."""
    from project_setup.catalog import selected_layers
    from project_setup.cli import load_preset

    reached: set[str] = set()
    for path in PRESETS.glob("*.yml"):
        reached |= set(selected_layers(catalog, load_preset(path.stem, PRESETS)[0]))
    assert set(catalog.layers) - reached == set()


# ------------------------------------------------------- placeholders and composition


def test_only_name_and_description_are_hard_required(catalog):
    """Setup time is the wrong moment to demand a production URL.

    Everything a user cannot know yet is a placeholder, so a scaffold is never
    blocked. Widening this set is a deliberate decision, not an accident.
    """
    required = {
        q.name for layer in catalog.layers.values() for q in layer.questions.values() if q.required
    }
    assert required == {"PROJECT_NAME", "DESCRIPTION"}


def test_placeholders_are_reported_not_enforced(catalog):
    problems = validate_data(catalog, {**IDENTITY, "WANT_API": True})
    assert [p for p in problems if p.level == "error"] == []
    holders = {p.key for p in problems if p.code == "PLACEHOLDER_IN_USE"}
    assert {"API_SERVER_URL", "ORG"} <= holders


def test_answering_a_placeholder_clears_its_warning(catalog):
    data = {**IDENTITY, "WANT_API": True, "API_SERVER_URL": "https://real.example.com"}
    holders = {p.key for p in validate_data(catalog, data) if p.code == "PLACEHOLDER_IN_USE"}
    assert "API_SERVER_URL" not in holders


def test_native_init_is_not_a_question(catalog):
    """The task skips when a manifest exists and warns when the tool is absent, so
    there is nothing for a user to decide."""
    assert "RUN_NATIVE_INIT" not in catalog.all_question_names()


def test_a_monorepo_flag_without_members_is_a_warning(catalog):
    """The combination renders cleanly and then lints and tests nothing.

    `.ci/members.json` with an empty members array used to replace every language
    job with no job at all. Silence was the wrong answer, and so is a refusal: the
    user may be one answer away from meaning it.
    """
    problems = validate_data(catalog, {**IDENTITY, "IS_MONOREPO": True})
    inert = [p for p in problems if p.code == "ANSWER_HAS_NO_EFFECT"]
    assert len(inert) == 1
    assert inert[0].level == "warning"
    assert inert[0].key == "MONOREPO_MEMBERS"
    assert [p for p in problems if p.level == "error"] == []


def test_a_monorepo_flag_with_members_is_silent(catalog):
    data = {
        **IDENTITY,
        "IS_MONOREPO": True,
        "MONOREPO_MEMBERS": '[{"name": "api", "path": "services/api", '
        '"capabilities": {"go": ["lint"]}}]',
    }
    assert [p for p in validate_data(catalog, data) if p.code == "ANSWER_HAS_NO_EFFECT"] == []


def test_defaults_for_skips_a_derived_expression(catalog):
    """A derived default is a Jinja expression, never a value to hand onward."""
    defaults = catalog.defaults_for(["hooks", "ci", "lang-python"])
    assert defaults["DEFAULT_BRANCH"] == "main"
    assert "PYTHON_VERSION_NODOT" not in defaults
    assert all("@@" not in str(v) for v in defaults.values())


def test_the_declared_placeholder_is_discoverable(catalog):
    """`validate` recognises this exact string and no other.

    A caller that substitutes its own stand-in -- `@owner` for `@TODO-owner` --
    reports a clean answer set and ships a CODEOWNERS file naming nobody, so the
    string itself has to be readable rather than guessable.
    """
    questions = catalog.questions_for(["governance", "forge"])
    assert questions["CODEOWNER"].placeholder == "@TODO-owner"
    problems = validate_data(catalog, {**IDENTITY, "CODEOWNER": "@TODO-owner"})
    assert any(p.code == "PLACEHOLDER_IN_USE" and p.key == "CODEOWNER" for p in problems)


def test_a_branch_answer_that_contradicts_the_checkout_is_a_warning(tmp_path):
    """DEFAULT_BRANCH reaches the CI triggers and release-please.

    `main` written into a checkout on `master` produced a repository whose pipelines
    watch a branch that does not exist, and nothing said so.
    """
    from project_setup.catalog import repo_conflicts

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/master\n")

    problems = repo_conflicts(tmp_path, {"DEFAULT_BRANCH": "main"})

    assert [p.code for p in problems] == ["ANSWER_CONTRADICTS_REPO"]
    assert problems[0].level == "warning"
    assert "master" in problems[0].message


def test_a_matching_branch_says_nothing(tmp_path):
    from project_setup.catalog import repo_conflicts

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/main\n")

    assert repo_conflicts(tmp_path, {"DEFAULT_BRANCH": "main"}) == []


def test_a_greenfield_destination_has_no_branch_to_contradict(tmp_path):
    from project_setup.catalog import repo_conflicts

    assert repo_conflicts(tmp_path, {"DEFAULT_BRANCH": "main"}) == []


def test_a_destination_that_cannot_hold_a_repository_is_named(tmp_path):
    """`mkdir` raised FileExistsError from inside pathlib, after writing the licence."""
    from project_setup.cli import unusable_dest

    a_file = tmp_path / "notadir"
    a_file.write_text("")

    assert "is a file" in (unusable_dest(a_file) or "")
    assert unusable_dest(tmp_path / "does/not/exist/yet") is None
    assert "which is a file" in (unusable_dest(a_file / "under/a/file") or "")


def test_a_stack_is_only_a_composition_of_parts(catalog):
    from project_setup.cli import load_preset

    data, origin = load_preset("desktop-rust-ts", PRESETS)
    assert data["WANT_LANG_RUST"] is True
    assert data["WANT_LANG_TS"] is True
    assert origin["WANT_LANG_RUST"] == "parts/lang-rust"
    assert origin["WANT_LANG_TS"] == "parts/lang-ts"
    assert origin["SPDX_ID"] == "parts/policy"


def test_a_later_part_overrides_an_earlier_one(catalog):
    from project_setup.cli import load_preset

    data, origin = load_preset("gitlab-service", PRESETS)
    assert data["FORGE_PLATFORM"] == "gitlab"
    assert origin["FORGE_PLATFORM"] == "parts/forge-gitlab"


def test_ad_hoc_composition_needs_no_stack(catalog):
    """The case that had no preset: a Rust core with a TypeScript front end."""
    from project_setup.catalog import selected_layers
    from project_setup.cli import load_preset

    data = {}
    for part in ("parts/policy", "parts/forge-github", "parts/lang-rust", "parts/lang-ts"):
        data.update(load_preset(part, PRESETS)[0])
    layers = selected_layers(catalog, data)
    assert {"lang-rust", "lang-ts"} <= set(layers)
    assert [p for p in validate_data(catalog, {**data, **IDENTITY}) if p.level == "error"] == []


def test_a_preset_cycle_is_refused(tmp_path):
    from project_setup.cli import load_preset

    (tmp_path / "a.yml").write_text("_extends: [b]\n")
    (tmp_path / "b.yml").write_text("_extends: [a]\n")
    with pytest.raises(SystemExit, match="cycle"):
        load_preset("a", tmp_path)


def test_a_value_the_template_would_reject_is_an_error_not_a_clean_answer_set(catalog):
    """`validate` reported OK and `apply` then died on the first layer.

    The whole point of `validate` is to be the cheap check that writes nothing, so a
    declared `validator:` it never evaluates makes it useless for the mistake most
    likely to be made: PROJECT_NAME lands in a crate, module and package name.
    """
    from project_setup.catalog import validate_data

    data = {"PROJECT_NAME": "Bad_Name", "DESCRIPTION": "y"}
    problems = [p for p in validate_data(catalog, data) if p.level == "error"]

    assert [p.code for p in problems] == ["INVALID_VALUE"]
    assert problems[0].key == "PROJECT_NAME"
    # The message is the whole of what a user gets, and it said only "must match
    # ^[a-z][a-z0-9-]+$" -- a regex to decode, with no example of a name that works.
    assert "^[a-z]" not in problems[0].message
    assert "lowercase" in problems[0].message
    assert "my-app" in problems[0].message


def test_the_name_rule_takes_one_letter_and_refuses_a_trailing_dash(catalog):
    """The old pattern had both ends wrong.

    `^[a-z][a-z0-9-]+$` needed two characters, so the legitimate crate name `q` was
    refused, and it ended anywhere, so the typo `my-app-` was accepted.
    """
    from project_setup.catalog import validate_data

    def rejected(name: str) -> bool:
        data = {"PROJECT_NAME": name, "DESCRIPTION": "y"}
        return any(p.key == "PROJECT_NAME" for p in validate_data(catalog, data))

    assert not rejected("q")
    assert not rejected("my-app")
    assert not rejected("api2")
    assert rejected("my-app-")
    assert rejected("My-App")
    assert rejected("my_app")
    assert rejected("2fast")


def test_a_valid_value_passes_its_validator_silently(catalog):
    from project_setup.catalog import validate_data

    data = {"PROJECT_NAME": "good-name", "DESCRIPTION": "y", "DEFAULT_BRANCH": "trunk"}

    assert [p for p in validate_data(catalog, data) if p.code == "INVALID_VALUE"] == []


def test_a_validator_is_checked_for_a_selected_layer_only(catalog):
    """I18N_PROJECT_DIR is declared by the i18n layer, which most projects omit."""
    from project_setup.catalog import validate_data

    base = {"PROJECT_NAME": "ok", "DESCRIPTION": "y", "I18N_PROJECT_DIR": "not a dir!"}
    assert [p for p in validate_data(catalog, base) if p.code == "INVALID_VALUE"] == []

    with_i18n = {**base, "WANT_I18N": True}
    codes = [p.code for p in validate_data(catalog, with_i18n) if p.level == "error"]
    assert "INVALID_VALUE" in codes


def test_a_defaulted_answer_is_validated_too(catalog):
    """DEFAULT_BRANCH defaults to `main`, and a default that fails is still a failure."""
    from project_setup.catalog import Question, validate_data

    questions = catalog.questions_for(["base"])
    assert questions["PROJECT_NAME"].validator
    assert Question("X").validator == ""
    # `main` is the layer default and passes, so nothing is reported for it.
    data = {"PROJECT_NAME": "ok", "DESCRIPTION": "y"}
    assert [p for p in validate_data(catalog, data) if p.key == "DEFAULT_BRANCH"] == []


def test_switching_forge_names_the_surface_the_switch_leaves_running(tmp_path):
    """FORGE_EXCLUDE stops the other forge being written; nothing deletes it.

    So a github project answered `gitlab` kept eight live GitHub workflow files,
    still triggering on push, while gen_caller reported no caller to write.
    """
    from project_setup.catalog import repo_conflicts

    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text("on: push\n")

    problems = repo_conflicts(tmp_path, {"FORGE_PLATFORM": "gitlab"})

    assert [p.code for p in problems] == ["STALE_FORGE_SURFACE"]
    assert ".github/workflows" in problems[0].message
    assert problems[0].level == "warning"


def test_the_selected_forges_own_surface_is_not_stale(tmp_path):
    from project_setup.catalog import repo_conflicts

    (tmp_path / ".github/workflows").mkdir(parents=True)

    assert repo_conflicts(tmp_path, {"FORGE_PLATFORM": "github"}) == []


def test_switching_away_from_gitlab_names_its_surface_too(tmp_path):
    from project_setup.catalog import repo_conflicts

    (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]\n")

    problems = repo_conflicts(tmp_path, {"FORGE_PLATFORM": "github"})

    assert [p.code for p in problems] == ["STALE_FORGE_SURFACE"]
    assert ".gitlab-ci.yml" in problems[0].message


def test_a_greenfield_destination_has_no_stale_forge_surface(tmp_path):
    from project_setup.catalog import repo_conflicts

    assert repo_conflicts(tmp_path, {"FORGE_PLATFORM": "gitlab"}) == []


def test_an_error_is_printed_before_the_warnings_nobody_has_to_act_on(capsys):
    """The one line that decides whether anything gets written came third of four."""
    from project_setup.cli import main

    code = main(
        [
            "validate",
            "--preset",
            "minimal",
            "--set",
            "PROJECT_NAME=Bad_Name",
            "--set",
            "DESCRIPTION=y",
        ]
    )
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  ")]

    assert code == 1
    assert lines[0].strip().startswith("ERROR")


def test_a_mapping_of_choices_validates_against_its_values(catalog):
    """Copier's {label: value} choice form lists what a human sees, not what an answer
    must equal. Comparing an answer against the mapping compares it against the
    labels, which rejected every real boolean the moment the asked bools became
    selects."""
    questions = catalog.questions_for(selected_layers(catalog, {"WANT_LANG_GO": True}))
    assert questions["GO_VENDOR"].choices == [False, True]
    assert questions["GO_VENDOR"].choice_labels is not None

    data = {**IDENTITY, "WANT_LANG_GO": True, "GO_VENDOR": True}
    assert [p for p in validate_data(catalog, data) if p.code == "INVALID_CHOICE"] == []

    bad = {**IDENTITY, "WANT_LANG_GO": True, "GO_VENDOR": "maybe"}
    assert [p for p in validate_data(catalog, bad) if p.code == "INVALID_CHOICE"]


def test_a_member_with_no_manifest_is_named_rather_than_left_to_a_green_ci(tmp_path):
    """The monorepo preset's CI jobs targeted services/api and apps/web, which no step
    creates: apply wrote the Go and TypeScript starters at the root, where no member job
    looks, and each job runs only when its own path changes.
    """
    from project_setup.catalog import repo_conflicts

    members = json.dumps(
        [
            {"name": "api", "path": "services/api", "capabilities": {"go": ["lint"]}},
            {"name": "web", "path": "apps/web", "capabilities": {"ts": ["test"]}},
        ]
    )
    (tmp_path / "services/api").mkdir(parents=True)
    (tmp_path / "services/api/go.mod").write_text("module api\n")

    problems = repo_conflicts(tmp_path, {"MONOREPO_MEMBERS": members})
    assert [(p.level, p.code, p.key) for p in problems] == [
        ("warning", "MEMBER_PATH_EMPTY", "MONOREPO_MEMBERS")
    ]
    assert "apps/web/package.json" in problems[0].message
    assert "services/api/go.mod" not in problems[0].message


def test_members_that_exist_and_a_single_root_repository_raise_nothing(tmp_path):
    from project_setup.catalog import repo_conflicts

    (tmp_path / "apps/web").mkdir(parents=True)
    (tmp_path / "apps/web/package.json").write_text("{}\n")
    members = '[{"name": "web", "path": "apps/web", "capabilities": {"ts": ["lint"]}}]'
    assert repo_conflicts(tmp_path, {"MONOREPO_MEMBERS": members}) == []
    assert repo_conflicts(tmp_path, {"MONOREPO_MEMBERS": "[]"}) == []
    assert repo_conflicts(tmp_path, {}) == []


def test_member_layers_drops_the_root_only_set(catalog):
    from project_setup.catalog import member_layers

    data = {"WANT_LANG_TS": True, "WANT_API": True}
    assert member_layers(catalog, data) == ["api", "lang-ts"]
    assert set(member_layers(catalog, data)) & set(ALWAYS_ON) == set()


def test_member_capabilities_reads_off_the_lang_layers():
    from project_setup.catalog import member_capabilities

    assert member_capabilities(["lang-go", "lang-ts", "api"]) == {
        "go": ["lint", "test"],
        "ts": ["lint", "test"],
    }
    assert member_capabilities(["api", "i18n"]) == {}


def test_a_plain_apply_under_an_existing_scaffold_root_is_refused(tmp_path):
    """This is the hazard the round exists to close: applying straight into a
    subdirectory of an already-scaffolded repository would write a second `.git`,
    LICENSE and the rest of the root-only surface. `--member` is the only way past
    it."""
    from project_setup.catalog import repo_conflicts

    root = tmp_path / "monorepo"
    member = root / "services" / "api"
    member.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    problems = repo_conflicts(member, {})
    assert [(p.level, p.code) for p in problems] == [("error", "NESTED_SCAFFOLD")]
    assert str(root) in problems[0].message

    assert repo_conflicts(member, {}, member=True) == []


def test_a_member_scoped_apply_needs_a_root_above_it(tmp_path):
    from project_setup.catalog import repo_conflicts

    lonely = tmp_path / "not-a-member"
    lonely.mkdir()

    problems = repo_conflicts(lonely, {}, member=True)
    assert [(p.level, p.code) for p in problems] == [("error", "MEMBER_NO_ROOT")]
    assert repo_conflicts(lonely, {}, member=False) == []


def test_recorded_answers_also_count_as_a_scaffold_root(tmp_path):
    """A root scaffolded before its first commit has no `.git` yet, but it does have
    the answers file apply always writes last."""
    from project_setup.catalog import ANSWERS_FILE, repo_conflicts

    root = tmp_path / "monorepo"
    member = root / "services" / "api"
    member.mkdir(parents=True)
    (root / ANSWERS_FILE).write_text("PROJECT_NAME: monorepo\n")

    assert [p.code for p in repo_conflicts(member, {})] == ["NESTED_SCAFFOLD"]


def test_a_members_own_answers_file_is_not_mistaken_for_the_root(tmp_path):
    """A member-scoped apply also writes ANSWERS_FILE at its own destination, for the
    re-apply diffing `deselected_layers` does per member. `find_scaffold_root` must
    skip a member's own file and keep climbing to the real root, or a member-of-a-
    member registers into the member's own orphaned `.ci/members.json` -- which no
    CI workflow reads -- under a path relative to the member rather than the root.

    Measured before the fix: `--member` into `root/services/api/sub` found
    `services/api` as "the root" and wrote `services/api/.ci/members.json` naming
    `sub` at path `sub`, leaving the real root's members.json untouched.
    """
    from project_setup.catalog import ANSWERS_FILE, find_scaffold_root

    root = tmp_path / "monorepo"
    member = root / "services" / "api"
    grandchild = member / "sub"
    grandchild.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (member / ANSWERS_FILE).write_text("PROJECT_NAME: api\n_MEMBER: true\n")

    assert find_scaffold_root(grandchild) == root
    assert find_scaffold_root(member) == root
