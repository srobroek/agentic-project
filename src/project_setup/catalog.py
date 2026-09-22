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

    def all_question_names(self) -> set[str]:
        names: set[str] = set()
        for layer in self.layers.values():
            names |= set(layer.questions)
        return names


def _parse_questions(cfg: dict) -> dict[str, Question]:
    out: dict[str, Question] = {}
    for key, spec in cfg.items():
        if key.startswith("_"):
            continue
        if not isinstance(spec, dict):
            # Copier's shorthand form: `NAME: default_value`
            out[key] = Question(name=key, default=spec)
            continue
        out[key] = Question(
            name=key,
            type=spec.get("type", "str"),
            help=spec.get("help", ""),
            default=spec.get("default"),
            choices=spec.get("choices"),
            required="default" not in spec,
            when=str(spec.get("when", "")),
        )
    return out


def load_catalog(templates_dir: Path) -> Catalog:
    if not templates_dir.is_dir():
        raise FileNotFoundError(f"templates directory not found: {templates_dir}")
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
            questions=_parse_questions(cfg),
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
        if key.startswith("_") or key in want_names or key in known:
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
        if q.choices and name in data and data[name] not in q.choices:
            problems.append(
                Problem(
                    "error",
                    "INVALID_CHOICE",
                    f"{data[name]!r} is not one of {q.choices}",
                    name,
                )
            )
    return problems


def _owners(catalog: Catalog, question: str, layers: list[str]) -> str:
    return ", ".join(n for n in layers if question in catalog.layers[n].questions)


def _owners_all(catalog: Catalog, question: str) -> list[str]:
    return [n for n, layer in catalog.layers.items() if question in layer.questions]
