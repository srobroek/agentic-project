"""Read the template layers and answer the two questions an agent needs answered:
what can be selected, and what must be answered once it is.

The layer configs are the only source of truth. Nothing here hardcodes a question.
"""

from __future__ import annotations

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

INTERVIEW = "_interview"
ANSWERS_FILE = ".project-setup-answers.yml"

# The interview's own gate for the tool-version questions. It reaches no template, so
# it is a known key rather than an unknown one when it appears in an answers file.
PIN_GATE = "PIN_TOOL_VERSIONS"


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
    choices: list | None = None
    required: bool = False
    when: str = ""
    # An obviously-fake default, so a scaffold is never blocked on a value nobody
    # knows yet. Reported after the fact instead.
    placeholder: str = ""
    # A default containing the template delimiter is computed at render time from
    # other answers. Reported separately so a reader cannot mistake the expression
    # for a literal value to pass through.
    derived_from: str = ""
    # False for a question the interview never asks because there is nothing to
    # decide: a value derived from another answer. Still settable with --set.
    asked: bool = True
    # A tool version. Asked only when the user says they want to set versions, so a
    # caller offers that choice once instead of reading out sixteen pins. Machine
    # readable so an agent can check rather than remember a prose rule.
    pinned: bool = False
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


def _parse_questions(
    cfg: dict,
    never_asked: frozenset[str] = frozenset(),
    pinned: frozenset[str] = frozenset(),
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
            choices=spec.get("choices"),
            required="default" not in spec,
            when=str(spec.get("when", "")),
            placeholder=str(spec.get("placeholder", "")),
            derived_from=derived_from,
            asked=key not in never_asked,
            pinned=key in pinned,
            validator=str(spec.get("validator", "")),
        )
    return out


def _interview_conditions(templates_dir: Path) -> dict[str, str]:
    """Each interview question's `when:`, which is where the gating actually lives.

    Reading it back is what lets `catalog --json` tell a caller what not to ask,
    instead of leaving that as a rule to remember.
    """
    cfg_path = templates_dir / INTERVIEW / "copier.yml"
    if not cfg_path.is_file():
        return {}
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    return {
        key: str(spec.get("when", ""))
        for key, spec in cfg.items()
        if not key.startswith("_") and isinstance(spec, dict)
    }


def load_catalog(templates_dir: Path) -> Catalog:
    if not templates_dir.is_dir():
        raise FileNotFoundError(f"templates directory not found: {templates_dir}")
    conditions = _interview_conditions(templates_dir)
    never_asked = frozenset(
        key for key, when in conditions.items() if when.strip().lower() in ("false", "no")
    )
    pinned = frozenset(key for key, when in conditions.items() if PIN_GATE in when)
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
            questions=_parse_questions(cfg, never_asked, pinned),
            has_tasks=bool(cfg.get("_tasks")),
        )
    if not layers:
        raise FileNotFoundError(f"no layers with a copier.yml under {templates_dir}")
    return Catalog(templates_dir=templates_dir, layers=layers)


def selected_layers(catalog: Catalog, data: dict) -> list[str]:
    """Layers to apply: the always-on set plus every WANT_<LAYER> that is true."""
    chosen = [n for n in ALWAYS_ON if n in catalog.layers]
    for key, value in data.items():
        if not key.startswith("WANT_") or not value:
            continue
        layer = layer_of(key)
        if layer in catalog.layers and layer not in chosen:
            chosen.append(layer)
    order = catalog.apply_order()
    return sorted(chosen, key=order.index)


@dataclass
class Problem:
    level: str  # "error" | "warning"
    code: str
    message: str
    key: str = ""

    def __str__(self) -> str:
        where = f" [{self.key}]" if self.key else ""
        return f"{self.level.upper()} {self.code}{where}: {self.message}"


def validate_data(catalog: Catalog, data: dict) -> list[Problem]:
    """Static checks that produce better messages than a template traceback.

    Copier remains the enforcer of choices and validators at render time; this
    catches the two classes it reports less legibly: an unanswered required
    question, and a key no layer will ever read.
    """
    problems: list[Problem] = []
    layers = selected_layers(catalog, data)
    known = catalog.all_question_names()
    want_names = {want_var(n) for n in catalog.optional_layers()}

    for key in sorted(data):
        if key.startswith("_") or key in want_names or key in known or key == PIN_GATE:
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
        problems.append(
            Problem(
                "error",
                "MISSING_REQUIRED",
                f"required by layer(s) {_owners(catalog, name, layers)} and has no default"
                + (f". {q.help}" if q.help else ""),
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


def repo_conflicts(dest: Path, data: dict) -> list[Problem]:
    """Answers that contradict the repository they are about to be written into.

    The scaffolder is standing in that checkout and can see the difference, so it
    reports it. A warning rather than an error in every case: the user may be one
    rename or one `git rm` away from meaning exactly what they answered.
    """
    return _branch_conflict(dest, data) + _stale_forge_surface(dest, data)


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
