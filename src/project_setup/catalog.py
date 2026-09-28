"""Read the template layers and answer the two questions an agent needs answered:
what can be selected, and what must be answered once it is.

The layer configs are the only source of truth. Nothing here hardcodes a question.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import jinja2
import yaml

# Layers applied to every project. Everything else is opt-in via WANT_<LAYER>.
ALWAYS_ON: tuple[str, ...] = (
    "base",
    "governance",
    "hooks",
    "just",
    "ci",
    "forge",
    "steering",
)

# What each layer is for, in one line. The multiselect listed ten bare directory
# names -- `worktrunk`, `a11y`, `infra-aws-cdk` -- and `catalog` listed seventeen of
# them with a question count, so the one thing a user needs in order to choose was
# the one thing neither said. Defined here and imported by tools/port_assets.py,
# which turns them into the multiselect's labels: two copies would drift.
LAYER_PURPOSE: dict[str, str] = {
    "base": "editorconfig, gitattributes, mise, the managed .gitignore",
    "governance": "LICENSE, CODEOWNERS, SECURITY, CONTRIBUTING, ADRs",
    "hooks": "pre-commit, commit-msg and pre-push guards",
    "just": "the task surface: setup, check, per-language recipes",
    "ci": "the workflow graph, derived from the layers present",
    "forge": "GitHub or GitLab: templates, policies, issue forms",
    "steering": "docs/agents/ plus AGENTS.md and CLAUDE.md",
    "release": "release-please: versioning, changelog, tags",
    "worktrunk": "parallel-agent worktrees and their setup hooks",
    "lang-go": "Go: golangci-lint, govulncheck, per-package tests",
    "lang-python": "Python: uv, ruff, ty, pytest, nox",
    "lang-ts": "TypeScript: bun, biome, oxlint, knip",
    "lang-rust": "Rust: clippy, nextest, deny, machete, llvm-cov",
    "api": "an OpenAPI contract, linted and diffed in CI",
    "i18n": "Inlang and Paraglide message catalogues",
    "a11y": "Playwright and axe scans of named routes",
    "infra-aws-cdk": "AWS CDK in TypeScript, in its own subtree",
}

INTERVIEW = "_interview"
ANSWERS_FILE = ".project-setup-answers.yml"

# Keys the interview owns. Each reaches no template, so each is a known key rather
# than an unknown one when it turns up in an answers file. Defined here and imported
# by tools/port_assets.py: two copies would silently disagree about what is a gate.
#
# PIN_GATE stands in for every tool version; TUNE_GATE for every default that is
# already right. SELECTION is the one multiselect that replaced ten yes/no prompts,
# and `selected_layers` reads it alongside the WANT_ booleans a preset sets.
PIN_GATE = "PIN_TOOL_VERSIONS"
TUNE_GATE = "CUSTOMIZE_DEFAULTS"
SELECTION = "LAYERS"
INTERVIEW_KEYS: frozenset[str] = frozenset({PIN_GATE, TUNE_GATE, SELECTION})


def want_var(layer: str) -> str:
    """WANT_ variable that selects a layer: 'lang-go' -> 'WANT_LANG_GO'."""
    return "WANT_" + layer.upper().replace("-", "_")


def layer_of(want: str) -> str:
    """Inverse of want_var: 'WANT_LANG_GO' -> 'lang-go'."""
    return want.removeprefix("WANT_").lower().replace("_", "-")


@dataclass(frozen=True)
class Question:
    name: str
    type: str = "str"
    help: str = ""
    default: object = None
    # Permitted values. Copier also accepts a {label: value} mapping, where the
    # values are what an answer must match -- the labels are only what a human sees
    # in the prompt. Both forms are normalised here, because comparing an answer
    # against a mapping compares it against the labels and rejects every real value.
    choices: list | None = None
    choice_labels: dict | None = None
    required: bool = False
    when: str = ""
    # An obviously-fake default, so a scaffold is never blocked on a value nobody
    # knows yet. Reported after the fact instead.
    placeholder: str = ""
    # A default containing the template delimiter is computed at render time from
    # other answers. Reported separately so a reader cannot mistake the expression
    # for a literal value to pass through.
    derived_from: str = ""
    # False for a question the interview never asks. Three different reasons, and a
    # caller has to tell them apart: `derived` is computed from another answer and
    # must not be passed through; `composed` is assembled from the conversation and
    # is exactly what a caller should supply; a `pinned` or `tuned` value is asked,
    # just behind one gate. All four stay settable with --set.
    asked: bool = True
    # A tool version. Asked only when the user says they want to set versions, so a
    # caller offers that choice once instead of reading out sixteen pins. Machine
    # readable so an agent can check rather than remember a prose rule.
    pinned: bool = False
    # A default that is already right -- a hook size limit, a job timeout, the
    # README's install line. Behind the single TUNE_GATE for the same reason.
    tuned: bool = False
    # A JSON artifact no human types at a one-line prompt: the ADR list, the
    # monorepo member list. Not asked at all, and the reason to involve a model.
    composed: bool = False
    # Copier's own `validator:` expression. Read back so `validate` can refuse an
    # answer set Copier would refuse at render time; without it `validate` reported
    # a clean answer set and the first layer then died on PROJECT_NAME.
    validator: str = ""

    @property
    def derived(self) -> bool:
        return bool(self.derived_from)


@dataclass
class Layer:
    name: str
    path: Path
    questions: dict[str, Question] = field(default_factory=dict)
    has_tasks: bool = False

    @property
    def optional(self) -> bool:
        return self.name not in ALWAYS_ON


@dataclass
class Catalog:
    templates_dir: Path
    layers: dict[str, Layer]

    @property
    def interview_path(self) -> Path:
        return self.templates_dir / INTERVIEW

    def optional_layers(self) -> list[str]:
        return sorted(n for n, layer in self.layers.items() if layer.optional)

    def apply_order(self) -> list[str]:
        """Always-on layers first, in declared order, then optional ones."""
        return [n for n in ALWAYS_ON if n in self.layers] + self.optional_layers()

    def questions_for(self, layers: list[str]) -> dict[str, Question]:
        merged: dict[str, Question] = {}
        for name in layers:
            merged.update(self.layers[name].questions)
        return merged

    def defaults_for(self, layers: list[str]) -> dict[str, object]:
        """The literal default of every question the given layers declare.

        Copier applies these itself at render time, but a generator argument is
        assembled outside Copier and still has to know the effective value. A
        derived default is skipped: it is a Jinja expression, not a value.
        """
        return {
            name: q.default
            for name, q in self.questions_for(layers).items()
            if q.default is not None and not q.derived
        }

    def all_question_names(self) -> set[str]:
        names: set[str] = set()
        for layer in self.layers.values():
            names |= set(layer.questions)
        return names


def _choice_values(choices: object) -> list | None:
    """The values an answer may take, from either Copier choice form.

    Copier accepts a list, or a {label: value} mapping where the values are what an
    answer must match and the labels are only what a human sees. Comparing an answer
    against the mapping compares it against the labels, which rejects every real
    value -- that is exactly what happened when the asked bools gained choices.
    """
    if choices is None:
        return None
    if isinstance(choices, dict):
        return list(choices.values())
    return list(choices)


def _parse_questions(
    cfg: dict,
    never_asked: frozenset[str] = frozenset(),
    pinned: frozenset[str] = frozenset(),
    tuned: frozenset[str] = frozenset(),
    composed: frozenset[str] = frozenset(),
) -> dict[str, Question]:
    out: dict[str, Question] = {}
    for key, spec in cfg.items():
        if key.startswith("_"):
            continue
        if not isinstance(spec, dict):
            # Copier's shorthand form: `NAME: default_value`
            out[key] = Question(name=key, default=spec)
            continue
        default = spec.get("default")
        derived_from = ""
        if isinstance(default, str) and "@@" in default:
            derived_from = default
            default = None
        out[key] = Question(
            name=key,
            type=spec.get("type", "str"),
            help=spec.get("help", ""),
            default=default,
            choices=_choice_values(spec.get("choices")),
            choice_labels=spec.get("choices") if isinstance(spec.get("choices"), dict) else None,
            required="default" not in spec,
            when=str(spec.get("when", "")),
            placeholder=str(spec.get("placeholder", "")),
            derived_from=derived_from,
            asked=key not in never_asked,
            pinned=key in pinned,
            tuned=key in tuned,
            composed=key in composed,
            validator=str(spec.get("validator", "")),
        )
    return out


def _interview_specs(templates_dir: Path) -> dict[str, dict]:
    """The generated interview's own question specs.

    The gating lives there, not in the layers, so reading it back is what lets
    `catalog --json` tell a caller what not to ask instead of leaving that as a rule
    to remember.
    """
    cfg_path = templates_dir / INTERVIEW / "copier.yml"
    if not cfg_path.is_file():
        return {}
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    return {
        key: spec for key, spec in cfg.items() if not key.startswith("_") and isinstance(spec, dict)
    }


def load_catalog(templates_dir: Path) -> Catalog:
    if not templates_dir.is_dir():
        raise FileNotFoundError(f"templates directory not found: {templates_dir}")
    specs = _interview_specs(templates_dir)
    conditions = {key: str(spec.get("when", "")) for key, spec in specs.items()}
    never_asked = frozenset(
        key for key, when in conditions.items() if when.strip().lower() in ("false", "no")
    )
    pinned = frozenset(key for key, when in conditions.items() if PIN_GATE in when)
    tuned = frozenset(key for key, when in conditions.items() if TUNE_GATE in when)
    # Not asked, and not a Jinja expression over other answers: an artifact assembled
    # from the conversation. The distinction matters to a caller -- a derived value
    # must not be passed through, a composed one is exactly what it should supply.
    composed = frozenset(
        key
        for key in never_asked
        if "@@" not in str(specs[key].get("default", "")) and key not in INTERVIEW_KEYS
    )
    layers: dict[str, Layer] = {}
    for child in sorted(templates_dir.iterdir()):
        if not child.is_dir() or child.name == INTERVIEW:
            continue
        cfg_path = child / "copier.yml"
        if not cfg_path.is_file():
            continue
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        layers[child.name] = Layer(
            name=child.name,
            path=child,
            questions=_parse_questions(cfg, never_asked, pinned, tuned, composed),
            has_tasks=bool(cfg.get("_tasks")),
        )
    if not layers:
        raise FileNotFoundError(f"no layers with a copier.yml under {templates_dir}")
    return Catalog(templates_dir=templates_dir, layers=layers)


def selected_layers(catalog: Catalog, data: dict) -> list[str]:
    """Layers to apply: the always-on set, plus whatever the answers select.

    Two spellings reach here, and both are first-class. A preset and `--set` name
    `WANT_<LAYER>` one layer at a time; the interview writes `LAYERS`, the one
    multiselect that replaced ten yes/no prompts. Neither is a fallback for the
    other: an interview seeded from a preset can record both, so this is a union.
    """
    chosen = [n for n in ALWAYS_ON if n in catalog.layers]

    def take(layer: str) -> None:
        if layer in catalog.layers and layer not in chosen:
            chosen.append(layer)

    for name in data.get(SELECTION) or []:
        take(str(name))
    for key, value in data.items():
        if key.startswith("WANT_") and value:
            take(layer_of(key))
    order = catalog.apply_order()
    return sorted(chosen, key=order.index)


def member_layers(catalog: Catalog, data: dict) -> list[str]:
    """Layers to apply into a monorepo member: `selected_layers`, minus the root-only set.

    ALWAYS_ON is exactly the root-only surface -- base (git_init, mise, the managed
    .gitignore), governance (LICENSE, CODEOWNERS), hooks, just, ci, forge, steering --
    so a member selects none of it. Measured: applying a language part straight into
    a member directory wrote a nested `.git`, a second LICENSE, CODEOWNERS,
    CONTRIBUTING.md, docs/agents/, AGENTS.md, CLAUDE.md and .github/, because that
    surface is applied unconditionally and no answer can exclude it.
    """
    return [n for n in selected_layers(catalog, data) if n not in ALWAYS_ON]


# gen_caller.py's accepted check kinds per language, restated nowhere else in this
# repo: both example members in presets/parts/monorepo.yml already ask for both.
MEMBER_CHECK_KINDS: tuple[str, ...] = ("lint", "test")


def member_capabilities(layers: list[str]) -> dict[str, list[str]]:
    """The MONOREPO_MEMBERS `capabilities` object implied by a member's own layers.

    `lang-go` -> `{"go": ["lint", "test"]}`, the same shape MEMBER_MANIFEST's keys
    already use. A member with no `lang-*` layer gets an empty object, which
    gen_caller.py refuses to register -- there is no CI job capability to describe.
    """
    return {
        layer.removeprefix("lang-"): list(MEMBER_CHECK_KINDS)
        for layer in layers
        if layer.startswith("lang-")
    }


def find_scaffold_root(dest: Path) -> Path | None:
    """The nearest ancestor of `dest` -- never `dest` itself -- that is a git
    repository or a recorded *root* scaffold.

    `dest` itself is excluded: applying into a repository's own root is the ordinary
    brownfield case, already supported. An *ancestor* holding one means `dest` is a
    subdirectory of something already scaffolded, which is what makes a plain apply's
    root-only surface -- a nested `.git` foremost -- a hazard rather than a normal
    write.

    A member's own answers file is skipped rather than accepted: `scaffold` writes
    `_MEMBER: true` into it, because member-scoped apply also writes ANSWERS_FILE at
    its own destination, for the re-apply diffing `deselected_layers` already does
    per member. Without the skip, `--member` into a path inside an existing member
    registered into *that member's own* `.ci/members.json` -- a file no CI workflow
    reads -- under the member's own relative path, instead of the true root's, and
    reported success.
    """
    for parent in dest.resolve().parents:
        if _is_git_repo(parent):
            return parent
        answers = parent / ANSWERS_FILE
        if answers.is_file() and not _is_member_scaffold(answers):
            return parent
    return None


def _is_member_scaffold(answers_file: Path) -> bool:
    recorded = yaml.safe_load(answers_file.read_text()) or {}
    return bool(isinstance(recorded, dict) and recorded.get("_MEMBER"))


def _is_git_repo(path: Path) -> bool:
    """A real repository, not just a directory that happens to be named `.git`.

    Measured on this machine: `/tmp/.git` exists with `hooks/` and `info/` but no
    `HEAD`, a stray template skeleton rather than a repository. `.exists()` alone
    made every destination under `/tmp` look nested inside one.
    """
    git = path / ".git"
    return (git / "HEAD").is_file() or git.is_file()


@dataclass
class Problem:
    level: str  # "error" | "warning"
    code: str
    message: str
    key: str = ""

    def __str__(self) -> str:
        where = f" [{self.key}]" if self.key else ""
        return f"{self.level.upper()} {self.code}{where}: {self.message}"


def validate_data(
    catalog: Catalog, data: dict, *, layers: list[str] | None = None
) -> list[Problem]:
    """Static checks that produce better messages than a template traceback.

    Copier remains the enforcer of choices and validators at render time; this
    catches the two classes it reports less legibly: an unanswered required
    question, and a key no layer will ever read.

    `layers` overrides which layers' own questions are checked as required or
    placeholder-bearing. A member-scoped apply passes `member_layers`: its answers
    never touch governance's CODEOWNER or hooks' SECURITY_CONTACT, and reporting
    those as still-a-placeholder named a question the member never asked.
    """
    problems: list[Problem] = []
    layers = selected_layers(catalog, data) if layers is None else layers
    known = catalog.all_question_names()
    want_names = {want_var(n) for n in catalog.optional_layers()}

    always_on_wants = {want_var(n): n for n in ALWAYS_ON}
    for key in sorted(data):
        if key.startswith("_") or key in want_names or key in known or key in INTERVIEW_KEYS:
            continue
        # `WANT_CI: false` is not a typo and not an unselected layer: it is somebody
        # trying to turn a layer off that is applied to every project. "no layer
        # declares this" sent them looking for a spelling mistake.
        if key in always_on_wants:
            problems.append(
                Problem(
                    "warning",
                    "ALWAYS_ON_LAYER",
                    f"the {always_on_wants[key]} layer is applied to every project, so this "
                    f"answer changes nothing. It cannot be deselected; a layer you do not "
                    f"want the output of is a question for the layer, not an answer.",
                    key,
                )
            )
            continue
        problems.append(
            Problem(
                "warning",
                "UNKNOWN_KEY",
                "no layer declares this; it will be ignored. Typo, or a layer you did not select?",
                key,
            )
        )

    for name, q in sorted(catalog.questions_for(layers).items()):
        if not q.required or name in data:
            continue
        # The message a user meets first, so it names the flag that answers it. It read
        # "required by layer(s) base and has no default. One-line purpose", which ran
        # the help onto the end of a sentence and left the reader to work out that
        # `--set` is how you supply it.
        problems.append(
            Problem(
                "error",
                "MISSING_REQUIRED",
                (f"{q.help}. " if q.help else "")
                + f"No default: layer(s) {_owners(catalog, name, layers)} need it. "
                + f"Supply it with --set {name}=<value>, a --data-file, or the interview.",
                name,
            )
        )

    for name, q in sorted(catalog.questions_for(layers).items()):
        if not q.placeholder:
            continue
        if str(data.get(name, q.placeholder)) == q.placeholder:
            problems.append(
                Problem(
                    "warning",
                    "PLACEHOLDER_IN_USE",
                    f"still the placeholder {q.placeholder!r}" + (f". {q.help}" if q.help else ""),
                    name,
                )
            )

    for name, q in sorted(catalog.questions_for(layers).items()):
        if q.choices and name in data and data[name] not in q.choices:
            problems.append(
                Problem(
                    "error",
                    "INVALID_CHOICE",
                    f"{data[name]!r} is not one of {q.choices}",
                    name,
                )
            )

    problems.extend(_failed_validators(catalog, data, layers))

    problems.extend(_inert_answers(data))
    return problems


def _validator_env() -> jinja2.Environment:
    """A Jinja environment matching the one Copier renders a validator in.

    `regex_search` comes from the Ansible filter extension Copier itself loads; the
    delimiters come from the layers' own `_envops`. Built the same way here so a
    validator either means the same thing in both places or fails loudly.
    """

    return jinja2.Environment(
        variable_start_string="@@",
        variable_end_string="@@",
        keep_trailing_newline=True,
        extensions=["jinja2_ansible_filters.AnsibleCoreFiltersExtension"],
        autoescape=False,
    )


def _failed_validators(catalog: Catalog, data: dict, layers: list[str]) -> list[Problem]:
    """Answers a declared `validator:` rejects.

    Copier enforces these at render time, which made `validate` report a clean answer
    set for `PROJECT_NAME=Bad_Name` and `apply` then fail on the very first layer.
    `validate` exists to be the cheap check that writes nothing, so it has to know.

    A validator renders to its message when it fails and to nothing when it passes.
    An expression this cannot evaluate is reported rather than swallowed: silently
    passing an unevaluated validator is the bug this function exists to remove.
    """
    questions = catalog.questions_for(layers)
    checked = {name: q for name, q in questions.items() if q.validator and not q.derived}
    if not checked:
        return []
    answers = {**catalog.defaults_for(layers), **{k: v for k, v in data.items()}}
    env = _validator_env()
    problems: list[Problem] = []
    for name, q in sorted(checked.items()):
        if answers.get(name) is None:
            continue
        try:
            message = env.from_string(q.validator).render(**answers).strip()
        except Exception as exc:  # noqa: BLE001 - an unevaluable validator is a finding
            problems.append(
                Problem(
                    "warning",
                    "VALIDATOR_NOT_CHECKED",
                    f"could not evaluate this question's validator ({type(exc).__name__}: "
                    f"{exc}), so Copier may still refuse the value at render time",
                    name,
                )
            )
            continue
        if message:
            problems.append(Problem("error", "INVALID_VALUE", message, name))
    return problems


def _inert_answers(data: dict) -> list[Problem]:
    """Answers that are accepted, render cleanly, and then do nothing.

    A warning rather than an error: the combination is legal and a user mid-setup
    may well be about to supply the other half. Saying nothing is the wrong trade,
    because the failure is invisible until CI runs and reports no jobs.
    """
    if not data.get("IS_MONOREPO"):
        return []
    members = str(data.get("MONOREPO_MEMBERS", "[]")).strip()
    if members not in ("", "[]"):
        return []
    return [
        Problem(
            "warning",
            "ANSWER_HAS_NO_EFFECT",
            "IS_MONOREPO is set but MONOREPO_MEMBERS lists nobody, so .ci/members.json "
            "names no member and CI stays single-root. Add members, or drop IS_MONOREPO.",
            "MONOREPO_MEMBERS",
        )
    ]


def repo_conflicts(dest: Path, data: dict, *, member: bool = False) -> list[Problem]:
    """Answers that contradict the repository they are about to be written into.

    The scaffolder is standing in that checkout and can see the difference, so it
    reports it. Everything below `_nested_scaffold` and `_member_without_root` is a
    warning: the user may be one rename or one `git rm` away from meaning exactly
    what they answered. Those two are errors, because nesting a repository or a
    second license is not something to warn about after the fact.
    """
    return (
        _nested_scaffold(dest, member=member)
        + _member_without_root(dest, member=member)
        + _branch_conflict(dest, data)
        + _stale_forge_surface(dest, data)
        + _members_without_manifest(dest, data)
    )


def _nested_scaffold(dest: Path, *, member: bool) -> list[Problem]:
    """A destination inside an existing scaffold, about to receive the root-only
    surface a second time.

    Measured: `project-setup apply --preset parts/lang-ts --dest R/services/api`
    after `R` was already scaffolded wrote a nested `.git`, a second LICENSE,
    CODEOWNERS, CONTRIBUTING.md, docs/agents/, AGENTS.md, CLAUDE.md and .github/ --
    the whole root-only surface, because ALWAYS_ON has no answer that excludes it.
    `--member` is the caller saying it knows, which drops that surface instead of
    writing it; refusing without it is the whole fix.
    """
    if member:
        return []
    root = find_scaffold_root(dest)
    if root is None:
        return []
    return [
        Problem(
            "error",
            "NESTED_SCAFFOLD",
            f"{dest} is inside the existing repository at {root}. A plain apply would "
            f"write a second .git, LICENSE, CODEOWNERS and the rest of the root-only "
            f"surface into it. Pass --member to scaffold this as a monorepo member "
            f"instead, which applies only the layers you selected.",
        )
    ]


def _member_without_root(dest: Path, *, member: bool) -> list[Problem]:
    """`--member` with nothing above `dest` to register it into.

    A member-scoped apply has to record itself somewhere: the root's own
    MONOREPO_MEMBERS and `.ci/members.json`. With no scaffolded root above `dest`
    there is nothing to update, and applying anyway would leave a directory with
    none of the root-only surface and no CI ever pointed at it.
    """
    if not member:
        return []
    if find_scaffold_root(dest) is not None:
        return []
    return [
        Problem(
            "error",
            "MEMBER_NO_ROOT",
            f"--member was given but no scaffolded root (a directory with .git or "
            f"{ANSWERS_FILE}) exists above {dest}. Scaffold the root first with "
            f"project-setup apply --preset <stack> --dest <root>, or drop --member if "
            f"{dest} is meant to be its own repository.",
        )
    ]


# The file a member needs before its CI job has anything to run against, per capability
# key in MONOREPO_MEMBERS. The same four languages gen_caller.py emits member jobs for.
MEMBER_MANIFEST: dict[str, str] = {
    "go": "go.mod",
    "ts": "package.json",
    "python": "pyproject.toml",
    "rust": "Cargo.toml",
}


def _members_without_manifest(dest: Path, data: dict) -> list[Problem]:
    """A member CI job pointed at a directory with nothing in it.

    The monorepo preset lists two example members, and apply writes each language's
    starter at the root, where no member job looks. Each job runs only when files
    under its member path change, so CI went green having linted and tested nothing,
    and the code at the root had no job at all. Visibility rather than a refusal: the
    member list is the one thing only its author knows, and a scaffold is the moment
    it is least likely to exist yet.
    """
    raw = data.get("MONOREPO_MEMBERS")
    try:
        members = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    if not isinstance(members, list):
        return []
    missing: list[str] = []
    for member in members:
        if not isinstance(member, dict) or not isinstance(member.get("path"), str):
            continue
        capabilities = member.get("capabilities")
        for language in capabilities if isinstance(capabilities, dict) else {}:
            manifest = MEMBER_MANIFEST.get(language)
            if manifest and not (dest / member["path"] / manifest).is_file():
                missing.append(f"{member['path']}/{manifest}")
    if not missing:
        return []
    return [
        Problem(
            "warning",
            "MEMBER_PATH_EMPTY",
            f"MONOREPO_MEMBERS expects {', '.join(missing)}, which {dest} does not have "
            f"yet. Each member job runs only when its own path changes, so CI checks "
            f"nothing there, and code outside every member has no job at all. Move the "
            f"code into each member path, or list the members that exist.",
            "MONOREPO_MEMBERS",
        )
    ]


def _branch_conflict(dest: Path, data: dict) -> list[Problem]:
    """DEFAULT_BRANCH reaches the CI workflows and release-please, so `main` written
    into a checkout on `master` produces a repository whose pipelines never trigger.
    """
    head = dest / ".git/HEAD"
    if not head.is_file():
        return []
    ref = head.read_text().strip()
    if not ref.startswith("ref: refs/heads/"):
        return []
    branch = ref.removeprefix("ref: refs/heads/")
    answered = str(data.get("DEFAULT_BRANCH", "")) or None
    if answered is None or answered == branch:
        return []
    return [
        Problem(
            "warning",
            "ANSWER_CONTRADICTS_REPO",
            f"DEFAULT_BRANCH is {answered!r} but the checkout at {dest} is on {branch!r}. "
            f"CI triggers and release-please read this, so they would watch a branch that "
            f"does not exist. Set DEFAULT_BRANCH={branch}, or rename the branch.",
            "DEFAULT_BRANCH",
        )
    ]


# The files each forge answer excludes. Selecting a forge swaps the CI surface for
# everything about to be written -- but Copier excludes files, it does not delete
# ones an earlier run already wrote.
FORGE_SURFACE: dict[str, tuple[str, ...]] = {
    "github": (".github/workflows",),
    "gitlab": (".gitlab/ci", ".gitlab-ci.yml"),
}


def _stale_forge_surface(dest: Path, data: dict) -> list[Problem]:
    """FORGE_PLATFORM changed, and the forge it turned off is still in the checkout.

    `FORGE_EXCLUDE` stops the unselected forge's files being *written*; nothing
    removes what a previous apply already wrote. So switching github to gitlab left
    eight live GitHub workflow files in place, still triggering on push, while
    `gen_caller` reported there was no caller to write. Naming the files is the whole
    fix: deleting a user's CI is not this tool's call to make.
    """
    answered = str(data.get("FORGE_PLATFORM", ""))
    if answered not in FORGE_SURFACE:
        return []
    stale = [
        relative
        for platform, paths in FORGE_SURFACE.items()
        if platform != answered
        for relative in paths
        if (dest / relative).exists()
    ]
    if not stale:
        return []
    other = next(p for p in FORGE_SURFACE if p != answered)
    return [
        Problem(
            "warning",
            "STALE_FORGE_SURFACE",
            f"FORGE_PLATFORM is {answered!r}, but {dest} still carries the {other} CI "
            f"surface: {', '.join(stale)}. Those files are excluded from this run, not "
            f"removed, and {other} keeps running them. Delete them to finish the switch.",
            "FORGE_PLATFORM",
        )
    ]


def _owners(catalog: Catalog, question: str, layers: list[str]) -> str:
    return ", ".join(n for n in layers if question in catalog.layers[n].questions)


def _owners_all(catalog: Catalog, question: str) -> list[str]:
    return [n for n, layer in catalog.layers.items() if question in layer.questions]
