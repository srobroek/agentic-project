"""Read the template layers and answer the two questions an agent needs answered:
what can be selected, and what must be answered once it is.

The layer configs are the only source of truth. Nothing here hardcodes a question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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

    problems.extend(_inert_answers(data))
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


def _owners(catalog: Catalog, question: str, layers: list[str]) -> str:
    return ", ".join(n for n in layers if question in catalog.layers[n].questions)


def _owners_all(catalog: Catalog, question: str) -> list[str]:
    return [n for n, layer in catalog.layers.items() if question in layer.questions]
