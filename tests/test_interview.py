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


def test_one_multiselect_replaces_ten_yes_no_prompts(interview):
    """Ten `Include the <layer> layer? (y/N)` prompts were ten of 27 questions.

    The list is asked; each WANT_<LAYER> is derived from it and never asked, so every
    preset, every `--set` and `selected_layers` still read the key they always read.
    """
    from port_assets import SELECTION_HELP

    from project_setup.catalog import SELECTION

    order = list(interview)
    assert order[2] == SELECTION
    assert interview[SELECTION]["multiselect"] is True
    assert interview[SELECTION]["help"] == SELECTION_HELP
    assert interview[SELECTION]["default"] == []

    wants = [n for n in order if n.startswith("WANT_")]
    assert len(wants) == len(interview[SELECTION]["choices"])
    # Declared straight after the list they read, and none of them is a prompt.
    assert wants == order[3 : 3 + len(wants)]
    for name in wants:
        assert is_asked(interview[name]) is False
        assert SELECTION in interview[name]["default"]


def test_the_widest_reaching_answer_is_asked_with_the_shape(interview):
    """FORGE_PLATFORM swaps every layer's CI surface, and was asked 27th of 27.

    Its position followed layer order, `forge` after `ci`, which put the answer with
    the largest blast radius behind every hook threshold and job timeout.
    """
    order = list(interview)
    assert order.index("FORGE_PLATFORM") < order.index("SPDX_ID")
    assert order.index("FORGE_PLATFORM") < order.index("DEFAULT_BRANCH")
    wants = [n for n in order if n.startswith("WANT_")]
    assert order.index("FORGE_PLATFORM") == order.index(wants[-1]) + 1


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


def test_no_tool_version_is_asked_unless_the_user_asks_to_set_them(interview):
    """One question stands in for the whole set.

    Asking sixteen versions unprompted was one mistake; never asking them was the
    other. A user who needs Python 3.12 or an older Rust says so once, and everybody
    else answers a single no.
    """
    pins = [
        name
        for name in interview
        if name.endswith("_VERSION") and name not in ("API_VERSION", "PYTHON_VERSION_NODOT")
    ]
    assert len(pins) >= 14, "expected the pinned toolchain set to be present"
    unguarded = [
        name for name in pins if "PIN_TOOL_VERSIONS" not in interview[name].get("when", "")
    ]
    assert unguarded == []
    assert all(is_asked(interview[name]) for name in pins), "a pin is asked, just not by default"


def test_the_version_gate_is_asked_once_and_before_every_version(interview):
    order = list(interview)
    assert interview["PIN_TOOL_VERSIONS"]["default"] is False
    assert interview["PIN_TOOL_VERSIONS"]["help"]
    first_version = min(
        order.index(n) for n in order if "PIN_TOOL_VERSIONS" in interview[n].get("when", "")
    )
    assert order.index("PIN_TOOL_VERSIONS") < first_version


def test_a_version_keeps_its_own_layer_gate_too(interview):
    """Opting into versions must not ask for a toolchain the project does not use."""
    assert interview["GO_VERSION"]["when"] == "@@ (WANT_LANG_GO) and PIN_TOOL_VERSIONS @@"
    assert interview["UV_VERSION"]["when"] == "@@ PIN_TOOL_VERSIONS @@"


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


def test_a_json_artifact_is_not_a_prompt(interview):
    """`[{"title": ..., "decision": ...}]` is not answerable in one line.

    Gating MONOREPO_MEMBERS on IS_MONOREPO fixed asking it of every project and left
    the real problem: a human typing a JSON array at a prompt that the terminal then
    truncated mid-schema. These are assembled from the conversation, which is what
    `composed` says, and `--set` and a data file still carry them.
    """
    for name in ("ADRS", "MONOREPO_MEMBERS"):
        assert is_asked(interview[name]) is False
        # Not a Jinja expression over other answers: a real value with a real default.
        assert "@@" not in str(interview[name]["default"])
    assert interview["MONOREPO_MEMBERS"]["default"] == "[]"
    assert interview["ADRS"]["default"] == "[]"


def test_a_language_question_is_gated_on_its_layer(interview):
    assert interview["GO_VENDOR"]["when"] == "@@ (WANT_LANG_GO) @@"
    assert interview["A11Y_SURFACES_JSON"]["when"] == "@@ (WANT_A11Y) @@"


def test_a_plain_project_answers_a_sane_number_of_questions(interview):
    """A minimal project answered 27 prompts; a PTY drive now counts eleven.

    Counted the way a user experiences it: a question with no `when:` is asked of
    everybody, whatever they selected. The bound is tight on purpose -- an eleventh
    unconditional question is a decision somebody should have to make deliberately,
    not one that lands because there was room.
    """
    unconditional = [n for n, spec in interview.items() if is_asked(spec) and not spec.get("when")]
    assert len(unconditional) <= 11, unconditional


def test_every_shipped_default_sits_behind_the_one_gate(interview):
    """Not never-asked and not asked of everybody: one question stands in for them.

    The same trade PIN_TOOL_VERSIONS made. Asking for a hook size limit and a job
    timeout unprompted was the wrong end of it; putting them out of reach is the other.
    """
    from project_setup.catalog import TUNE_GATE

    order = list(interview)
    gated = [n for n in order if TUNE_GATE in str(interview[n].get("when", ""))]

    assert interview[TUNE_GATE]["default"] is False
    assert interview[TUNE_GATE]["help"]
    assert order.index(TUNE_GATE) < min(order.index(n) for n in gated)
    assert set(gated) == {
        "CODE_OF_CONDUCT_CONTACT",
        "COMMIT_SCOPES",
        "HOOK_EXCLUDE_PATTERNS",
        "INSTALL_COMMANDS",
        "JOB_TIMEOUT_MINUTES",
        "MAX_FILE_KB",
        "USAGE_EXAMPLE",
    }
    assert all(is_asked(interview[n]) for n in gated), "a knob is asked, just not by default"


def test_the_catalog_reports_what_it_does_not_ask_by_default():
    """An agent should be able to check, not remember, which questions to skip.

    `pinned` and `asked` are different facts: a pin is a real question behind one
    gate, a derived value is not a question at all.
    """
    catalog = load_catalog(TEMPLATES)
    questions = catalog.questions_for(["lang-python", "api"])
    assert questions["PYTHON_VERSION"].pinned is True
    assert questions["PYTHON_VERSION"].asked is True
    assert questions["PYTHON_VERSION_NODOT"].asked is False
    assert questions["PYTHON_VERSION_NODOT"].pinned is False
    assert questions["API_VERSION"].pinned is False
    assert questions["API_VERSION"].asked is True
    assert questions["API_SERVER_URL"].asked is True


def test_the_version_gate_is_not_reported_as_an_unknown_answer():
    """The interview writes it into the answers file `apply` then validates.

    It reaches no template on purpose, so the naive UNKNOWN_KEY rule would warn
    about an answer the tool itself produced.
    """
    from project_setup.catalog import validate_data

    catalog = load_catalog(TEMPLATES)
    data = {"PROJECT_NAME": "x", "DESCRIPTION": "y", "PIN_TOOL_VERSIONS": True}
    assert [p for p in validate_data(catalog, data) if p.code == "UNKNOWN_KEY"] == []


def test_a_supplied_answer_the_interview_skips_is_still_recorded(tmp_path):
    """Copier records only what it asked, and it does not ask a pin by default.

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


def test_a_truncated_answer_says_it_is_truncated():
    """A silent cut at a fixed column reads as the whole value.

    `mise install && go mod download && bun insta` looks like a runnable command,
    and a cut JSON array looks like malformed JSON.
    """
    from project_setup.cli import answer_display

    shown = answer_display("mise install && go mod download && bun install", width=20)
    assert shown.endswith("\u2026")
    assert len(shown) == 20
    assert answer_display("short", width=20) == "short"


def test_a_boolean_answer_prints_the_way_yaml_writes_it():
    """These lines get copied into an answers file, where `False` is a string."""
    from project_setup.cli import answer_display

    assert answer_display(True) == "true"
    assert answer_display(False) == "false"


def test_no_prompt_is_cut_mid_word_by_an_eighty_column_terminal(interview):
    """Copier prints the mic, a space, and the help on one line, and prompt_toolkit
    truncates that line to the terminal width with no marker.

    Measured by driving the interview through an 80-column PTY: 77 help characters
    survived, so `...which Renovate bumps` reached the user as `...which Renova` and
    the ADRS schema stopped at `...alternatives, consequen`. Nine always-asked
    questions were over the line.
    """
    from port_assets import MAX_HELP_CHARS

    over = {
        name: len(spec["help"])
        for name, spec in interview.items()
        if is_asked(spec) and len(str(spec.get("help", ""))) > MAX_HELP_CHARS
    }
    assert over == {}


def test_the_budget_matches_what_an_eighty_column_prompt_shows(interview):
    """A constant nobody can re-derive drifts. 80 columns, less the mic and space."""
    from port_assets import MAX_HELP_CHARS, PROMPT_DECORATION

    assert PROMPT_DECORATION == 3
    assert MAX_HELP_CHARS == 76


def test_the_port_refuses_help_that_would_be_truncated():
    from port_assets import MAX_HELP_CHARS, _check_help_fits_a_prompt

    _check_help_fits_a_prompt({"OK": {"help": "x" * MAX_HELP_CHARS}})
    # `when: false` is never prompted, so its help has no width to overflow.
    _check_help_fits_a_prompt({"DERIVED": {"help": "x" * 200, "when": "false"}})

    with pytest.raises(SystemExit) as raised:
        _check_help_fits_a_prompt({"TOO_LONG": {"help": "x" * (MAX_HELP_CHARS + 1)}})
    assert "TOO_LONG" in str(raised.value)
    assert "TOKEN_POLICY" in str(raised.value)


def test_a_placeholder_prompt_does_not_repeat_what_it_already_shows(interview):
    """The prompt pre-fills the placeholder and both `validate` and the end of
    `apply` name every one still in use. A third sentence saying so only pushed the
    informative half of the help off the line.
    """
    assert interview["CODEOWNER"]["default"] == "@TODO-owner"
    assert interview["CODEOWNER"]["placeholder"] == "@TODO-owner"
    repeated = [
        name
        for name, spec in interview.items()
        if "A placeholder is fine" in str(spec.get("help", ""))
    ]
    assert repeated == []


def test_the_interview_hands_over_a_command_that_names_its_destination(interview):
    """`apply --data-file .project-setup-answers.yml` defaults --dest to the working
    directory, so the line the user copies scaffolded wherever they were standing.

    The message Copier prints cannot know the destination; the CLI can, so the
    command belongs there and the template message states only the fact.
    """
    cfg = yaml.safe_load(INTERVIEW.read_text())
    assert "project-setup apply" not in cfg["_message_after_copy"]


def test_the_handover_names_the_destination_and_the_whole_answers_path(tmp_path):
    """Every verb, with --dest and the absolute answers file, ready to paste.

    Relative to a destination the user is not standing in, the old line either
    failed on a missing data file or -- with an answers file already in the working
    directory -- scaffolded that repository instead.
    """
    from project_setup.cli import next_commands

    dest = tmp_path / "somewhere else"
    printed = next_commands(dest / ".project-setup-answers.yml", str(dest))

    lines = [line for line in printed.splitlines() if "project-setup" in line]
    assert [line.split()[1] for line in lines] == ["validate", "plan", "apply"]
    for line in lines:
        # Quoted, because a destination with a space in it is a single argument.
        assert f"--dest '{dest}'" in line
        assert f"--data-file '{dest / '.project-setup-answers.yml'}'" in line


def test_the_catalog_tells_composed_apart_from_derived():
    """Both are `asked: false`, and a caller must do opposite things with them.

    A derived value is a Jinja expression over another answer and passing it through
    lands the expression in a file. A composed value is the one class a caller is
    expected to assemble and supply.
    """
    catalog = load_catalog(TEMPLATES)
    questions = catalog.questions_for(["governance", "hooks", "ci", "lang-python"])

    assert questions["ADRS"].composed is True
    assert questions["ADRS"].derived is False
    assert questions["ADRS"].asked is False
    assert questions["MONOREPO_MEMBERS"].composed is True

    assert questions["PYTHON_VERSION_NODOT"].derived is True
    assert questions["PYTHON_VERSION_NODOT"].composed is False

    assert questions["MAX_FILE_KB"].tuned is True
    assert questions["MAX_FILE_KB"].asked is True
    assert questions["PYTHON_VERSION"].pinned is True
    assert questions["PYTHON_VERSION"].tuned is False


def test_the_interview_keys_are_not_reported_as_unknown_answers():
    """The interview writes all three into the answers file `apply` then validates."""
    from project_setup.catalog import INTERVIEW_KEYS, validate_data

    catalog = load_catalog(TEMPLATES)
    data = {"PROJECT_NAME": "x", "DESCRIPTION": "y", **{k: True for k in INTERVIEW_KEYS}}
    data["LAYERS"] = ["lang-ts"]

    assert [p for p in validate_data(catalog, data) if p.code == "UNKNOWN_KEY"] == []


def test_the_multiselect_selects_the_same_layers_the_booleans_do():
    """Both spellings reach `selected_layers`, and neither is a fallback."""
    from project_setup.catalog import selected_layers

    catalog = load_catalog(TEMPLATES)
    by_list = selected_layers(catalog, {"LAYERS": ["lang-rust", "release"]})
    by_flags = selected_layers(catalog, {"WANT_LANG_RUST": True, "WANT_RELEASE": True})

    assert by_list == by_flags
    assert "lang-rust" in by_list and "release" in by_list
    # A union, because an interview seeded from a preset can record both.
    both = selected_layers(catalog, {"LAYERS": ["release"], "WANT_LANG_RUST": True})
    assert both == by_list


def test_a_preset_pre_selects_its_layers_and_stays_deselectable():
    """Handing Copier the booleans as well would make them win over the multiselect.

    Supplied data beats a rendered default, so the list would show the preset's
    layers and then ignore every deselection the user made in the prompt.
    """
    from project_setup.cli import seed_selection

    catalog = load_catalog(TEMPLATES)
    seeded = seed_selection(
        catalog, {"PROJECT_NAME": "x", "WANT_LANG_TS": True, "WANT_RELEASE": True}
    )

    assert seeded["LAYERS"] == ["lang-ts", "release"]
    assert [k for k in seeded if k.startswith("WANT_")] == []
    assert seeded["PROJECT_NAME"] == "x"
    # Nothing supplied means nothing to seed; the interview asks from scratch.
    assert seed_selection(catalog, {}) == {}
