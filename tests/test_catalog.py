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

IDENTITY = {
    "PROJECT_NAME": "my-app",
    "DESCRIPTION": "A thing",
    "CODEOWNER": "@me",
    "SECURITY_CONTACT": "security@example.com",
}


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


# Answers that genuinely cannot be preset, beyond the four identity ones. Each is
# documented in the preset itself; the test exists so an undocumented gap cannot creep in.
DOCUMENTED_GAPS: dict[str, set[str]] = {
    "api-service": {"ORG", "API_SERVER_URL"},
    "web-app": {"INLANG_MESSAGE_FORMAT_MODULE_URL"},
}


@pytest.mark.parametrize("preset", sorted(p.stem for p in PRESETS.glob("*.yml")))
def test_a_preset_leaves_only_identity_and_documented_gaps(preset, catalog):
    """A preset must define a complete shape. Anything it cannot answer is declared."""
    import yaml

    data = yaml.safe_load((PRESETS / f"{preset}.yml").read_text()) or {}
    errors = [p for p in validate_data(catalog, {**data, **IDENTITY}) if p.level == "error"]
    gaps = {p.key for p in errors}
    assert gaps <= DOCUMENTED_GAPS.get(preset, set()), (
        f"{preset} has undeclared gaps: {sorted(gaps - DOCUMENTED_GAPS.get(preset, set()))}"
    )


def test_every_layer_is_reachable_from_some_preset(catalog):
    """A layer no preset selects is a layer nobody will discover."""
    import yaml

    from project_setup.catalog import selected_layers

    reached: set[str] = set()
    for path in PRESETS.glob("*.yml"):
        reached |= set(selected_layers(catalog, yaml.safe_load(path.read_text()) or {}))
    assert set(catalog.layers) - reached == set()
