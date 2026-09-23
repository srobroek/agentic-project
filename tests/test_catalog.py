"""Tests for the catalog contract: selection, validation, and the question set.

These are the guarantees an agent depends on, so they are tested rather than assumed.
"""

from __future__ import annotations

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
