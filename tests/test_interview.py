"""Tests for the interactive interview: what it asks, in what order, and what it will not.

The interview is generated from the layer configs by `tools/port_assets.py`, so these
assert properties of the generated question set. Every one of them was a real defect
found by driving `project-setup interview` through a PTY.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from project_setup.catalog import load_catalog

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
INTERVIEW = TEMPLATES / "_interview/copier.yml"


@pytest.fixture(scope="module")
def interview() -> dict[str, dict]:
    cfg = yaml.safe_load(INTERVIEW.read_text())
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def is_asked(spec: dict) -> bool:
    return str(spec.get("when", "")).strip().lower() != "false"


def test_identity_comes_before_anything_else(interview):
    """PROJECT_NAME used to be question 24 of 24, behind every A- and B-prefixed token.

    Alphabetical order is what put it there. A user who typed the command already
    knows the name and the purpose; nothing else is answerable before the shape is.
    """
    order = list(interview)
    assert order[0] == "PROJECT_NAME"
    assert order[1] == "DESCRIPTION"


def test_layer_selection_comes_before_the_questions_it_gates(interview):
    order = list(interview)
    wants = [n for n in order if n.startswith("WANT_")]
    assert wants == order[2 : 2 + len(wants)]


def test_a_gated_question_is_declared_after_its_gate(interview):
    """Copier evaluates `when:` in declaration order.

    A forward reference is not an error there, it is silently undefined, so the
    question is asked when it should be skipped or skipped when it should be asked,
    with nothing on screen to say which.
    """
    seen: set[str] = set()
    for name, spec in interview.items():
        condition = str(spec.get("when", ""))
        for referenced in interview:
            if referenced in condition:
                assert referenced in seen, f"{name} gates on {referenced}, asked later"
        seen.add(name)


def test_no_pinned_tool_version_is_ever_asked(interview):
    """They are pinned in the layers and Renovate bumps them, which the skill says.

    The interactive interview asked fourteen of them anyway.
    """
    pins = [
        name
        for name in interview
        if name.endswith("_VERSION") and name not in ("API_VERSION", "PYTHON_VERSION_NODOT")
    ]
    assert len(pins) >= 14, "expected the pinned toolchain set to be present"
    assert [name for name in pins if is_asked(interview[name])] == []


def test_the_contract_version_is_still_asked(interview):
    """API_VERSION is the project's own contract version, not a tool pin."""
    assert is_asked(interview["API_VERSION"])


def test_no_derived_value_is_ever_asked(interview):
    derived = [
        name
        for name, spec in interview.items()
        if isinstance(spec.get("default"), str) and "@@" in spec["default"]
    ]
    assert "PYTHON_VERSION_NODOT" in derived
    assert [name for name in derived if is_asked(interview[name])] == []


def test_every_asked_question_carries_help(interview):
    """Without help, questionary prints the bare token name as the prompt.

    `DEFAULT_BRANCH` and `MAX_FILE_KB` as questions tell a user nothing.
    """
    bare = [name for name, spec in interview.items() if is_asked(spec) and not spec.get("help")]
    assert bare == []


def test_monorepo_members_is_gated_on_the_flag_that_makes_it_meaningful(interview):
    """It was asked of every project, monorepo or not, as a raw JSON array."""
    assert interview["MONOREPO_MEMBERS"]["when"] == "@@ IS_MONOREPO @@"
    assert list(interview).index("IS_MONOREPO") < list(interview).index("MONOREPO_MEMBERS")


def test_a_language_question_is_gated_on_its_layer(interview):
    assert interview["GO_VENDOR"]["when"] == "@@ (WANT_LANG_GO) @@"
    assert interview["A11Y_SURFACES_JSON"]["when"] == "@@ (WANT_A11Y) @@"


def test_a_plain_project_answers_a_sane_number_of_questions(interview):
    """Deselecting every optional layer has to actually shorten the interview."""
    unconditional = [n for n, spec in interview.items() if is_asked(spec) and not spec.get("when")]
    assert len(unconditional) <= 30, unconditional


def test_the_catalog_reports_which_questions_are_never_asked():
    """An agent should be able to check, not remember, which questions to skip."""
    catalog = load_catalog(TEMPLATES)
    questions = catalog.questions_for(["lang-python", "api"])
    assert questions["PYTHON_VERSION"].asked is False
    assert questions["PYTHON_VERSION_NODOT"].asked is False
    assert questions["API_VERSION"].asked is True
    assert questions["API_SERVER_URL"].asked is True


def test_a_supplied_answer_the_interview_skips_is_still_recorded(tmp_path):
    """Copier records only what it asked, and it never asks a pin.

    So a preset that pins PYTHON_VERSION handed to `interview` produced an answers
    file without it, and `apply --data-file` then silently used the layer default.
    """
    from project_setup.cli import preserve_unasked

    answers = tmp_path / ".project-setup-answers.yml"
    answers.write_text("PROJECT_NAME: my-app\nDESCRIPTION: A thing\n_src_path: x\n")

    kept = preserve_unasked(
        answers,
        {"PROJECT_NAME": "my-app", "DESCRIPTION": "A thing", "PYTHON_VERSION": "3.12"},
    )

    assert kept == ["PYTHON_VERSION"]
    recorded = yaml.safe_load(answers.read_text())
    assert recorded["PYTHON_VERSION"] == "3.12"
    assert recorded["PROJECT_NAME"] == "my-app"


def test_nothing_is_appended_when_every_answer_was_recorded(tmp_path):
    from project_setup.cli import preserve_unasked

    answers = tmp_path / ".project-setup-answers.yml"
    answers.write_text("PROJECT_NAME: my-app\n")
    before = answers.read_text()

    assert preserve_unasked(answers, {"PROJECT_NAME": "my-app"}) == []
    assert answers.read_text() == before
