"""project-setup: scaffold a repository from layered Copier templates.

Designed to be driven by a human or an agent with the same commands. Every
subcommand takes --json so an agent never has to parse prose.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import copier
import yaml
from copier.errors import CopierAnswersInterrupt, InteractiveSessionError

from . import __version__
from .catalog import (
    ANSWERS_FILE,
    LAYER_PURPOSE,
    PIN_GATE,
    SELECTION,
    TUNE_GATE,
    Catalog,
    Problem,
    Question,
    find_scaffold_root,
    load_catalog,
    member_capabilities,
    member_layers,
    repo_conflicts,
    selected_layers,
    validate_data,
    want_var,
)
from .runner import (
    INCOMPLETE_FILE,
    MEMBERS_JSON,
    Changes,
    classify,
    deselected_layers,
    orphaned_files,
    register_member,
    rehearse,
    scaffold,
    snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_TEMPLATES = "PROJECT_SETUP_TEMPLATES"
ENV_PRESETS = "PROJECT_SETUP_PRESETS"
# The exact package name, not a suffix: `project-setup` is also the name of an older,
# unrelated plugin, and matching `*/project-setup` would scaffold from its layers.
PLUGIN_NAME = "@srobroek/project-setup"


@functools.cache
def installed_plugin() -> Path | None:
    """Where OMP says the plugin is installed right now, or None.

    Asked on every run rather than recorded, because a recorded path is exactly the
    stale copy the no-bundling rule exists to prevent: the plugin upgrades on its own.
    Never derived from `$0` or the working directory -- `$0` in an agent's shell is
    the shell, and the working directory is the user's target repository.
    """
    omp = shutil.which("omp")
    if omp is None:
        return None
    try:
        proc = subprocess.run(
            [omp, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        listing = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    for entry in listing.get("npm", []) if isinstance(listing, dict) else []:
        if entry.get("name") == PLUGIN_NAME and entry.get("enabled", True) and entry.get("path"):
            return Path(entry["path"])
    return None


def plugin_version_note(plugin: Path) -> str | None:
    """A warning when the plugin's layers are newer or older than this CLI.

    Both halves ship from the same repository, but the CLI is installed once and the
    plugin upgrades on its own, so after an upgrade the templates can expect a CLI
    that is not the one running them.
    """
    try:
        version = json.loads((plugin / "package.json").read_text()).get("version")
    except (OSError, ValueError):
        return None
    if version in (None, __version__):
        return None
    return (
        f"warning: the {PLUGIN_NAME} plugin is {version} and this CLI is {__version__}. "
        f"Reinstall the CLI from the plugin: uv tool install --reinstall {plugin}"
    )


def resolve_data_dir(kind: str, explicit: Path | None) -> Path:
    """Find the templates or presets directory.

    The templates are the plugin's payload, not the CLI's. They are deliberately not
    bundled into the wheel: the plugin can be upgraded on its own, and a bundled copy
    would go stale without saying so. In order of precedence:

      1. --templates / --presets
      2. PROJECT_SETUP_TEMPLATES / PROJECT_SETUP_PRESETS
      3. a source checkout, when running from one
      4. the plugin OMP reports as installed, asked at run time

    The fourth is what makes the common case free: outside a checkout every command
    used to need both flags, threaded through by hand. Anything else is an error that
    names every source, because a wrong guess here scaffolds the wrong layer set.
    """
    env_var = ENV_TEMPLATES if kind == "templates" else ENV_PRESETS
    candidates: list[tuple[str, Path]] = []
    if explicit is not None:
        candidates.append((f"--{kind}", explicit))
    if os.environ.get(env_var):
        candidates.append((env_var, Path(os.environ[env_var]).expanduser()))
    candidates.append(("source checkout", REPO_ROOT / kind))

    for _origin, path in candidates:
        if path.is_dir():
            return path
    plugin = installed_plugin()
    if plugin is not None and (plugin / kind).is_dir():
        if kind == "templates" and (note := plugin_version_note(plugin)) is not None:
            print(note, file=sys.stderr)
        return plugin / kind
    candidates.append(
        (f"OMP plugin {PLUGIN_NAME}", plugin / kind if plugin else Path("(not installed)"))
    )

    tried = "\n".join(f"    {origin}: {path}" for origin, path in candidates)
    raise SystemExit(
        f"error: no {kind} directory found. Tried:\n{tried}\n\n"
        f"  Install the plugin with OMP, or point the CLI at a {kind} directory,\n"
        f"  either per run:\n"
        f"    project-setup --{kind} /path/to/plugin/{kind} ...\n"
        f"  or once, for the shell:\n"
        f"    export {env_var}=/path/to/plugin/{kind}"
    )


# --------------------------------------------------------------- data loading


def load_data(args: argparse.Namespace, catalog: Catalog | None = None) -> dict:
    """Build the answer set from presets, a data file, and --set overrides.

    Sources compose, later winning, so a stack can be assembled from parts and then
    adjusted: presets in the order given, then the data file, then each --set.
    """
    data: dict = {}
    for name in getattr(args, "preset", None) or []:
        data.update(load_preset(name, args.presets)[0])
    if getattr(args, "data_file", None):
        path = Path(args.data_file)
        if not path.is_file():
            raise SystemExit(f"data file not found: {path}")
        data.update(yaml.safe_load(path.read_text()) or {})
    declared = {}
    if catalog is not None:
        for layer in catalog.layers.values():
            declared.update({q.name: q.type for q in layer.questions.values()})
    for pair in getattr(args, "set", None) or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects KEY=VALUE, got {pair!r}")
        key, _, raw = pair.partition("=")
        key = key.strip()
        data[key] = parse_scalar(raw, declared.get(key))
    # Set here rather than per command so plan, validate and apply cannot disagree about
    # it. The flag is the command's intent, so it wins over a --set: a member-scoped apply
    # whose layers exclude the root fragments must also exclude them from the render.
    if getattr(args, "member", False):
        data["IS_MEMBER"] = True
    # Copier records provenance keys in the answers file; they are not questions.
    return {k: v for k, v in data.items() if not k.startswith("_")}


def parse_scalar(raw: str, declared_type: str | None = None) -> object:
    """Parse a --set value, respecting the question's declared type.

    A question declared `str` keeps its raw text. That matters for the answers that
    carry JSON -- ADRS, MONOREPO_MEMBERS, LOCALES_JSON, the A11Y_*_JSON pair. YAML
    would happily turn those into Python objects, which then render as a Python repr
    with single quotes and land in the file as invalid JSON.
    """
    if declared_type == "str":
        return raw
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    return raw if parsed is None and raw != "" else parsed


EXTENDS_KEY = "_extends"


def load_preset(
    name: str, presets_dir: Path, _chain: tuple[str, ...] = ()
) -> tuple[dict, dict[str, str]]:
    """Resolve a preset and everything it extends.

    Returns the merged answers and, per key, the preset that last set it. A preset
    lists its bases under `_extends`; they are merged depth-first and first, so the
    extending preset always wins over what it builds on.
    """
    if name in _chain:
        raise SystemExit(f"preset cycle: {' -> '.join((*_chain, name))}")

    raw = yaml.safe_load(resolve_preset(name, presets_dir).read_text()) or {}
    bases = raw.pop(EXTENDS_KEY, []) or []
    if isinstance(bases, str):
        bases = [bases]

    merged: dict = {}
    origin: dict[str, str] = {}
    for base in bases:
        base_data, base_origin = load_preset(base, presets_dir, (*_chain, name))
        merged.update(base_data)
        origin.update(base_origin)
    for key, value in raw.items():
        merged[key] = value
        origin[key] = name
    return merged, origin


def resolve_preset(name: str, presets_dir: Path) -> Path:
    for candidate in (presets_dir / name, presets_dir / f"{name}.yml"):
        if candidate.is_file():
            return candidate
    stacks = sorted(p.stem for p in presets_dir.glob("*.yml"))
    parts = sorted(f"parts/{p.stem}" for p in (presets_dir / "parts").glob("*.yml"))
    raise SystemExit(
        f"unknown preset {name!r}.\n"
        f"  stacks: {', '.join(stacks) or 'none'}\n"
        f"  parts : {', '.join(parts) or 'none'}\n\n"
        f"  Presets compose, so a shape with no stack of its own is still reachable:\n"
        f"    project-setup apply --preset parts/lang-rust --preset parts/lang-ts ..."
    )


# --------------------------------------------------------------- subcommands


def cmd_catalog(args: argparse.Namespace, catalog: Catalog) -> int:
    if args.json:
        payload = {
            "always_on": [n for n in catalog.apply_order() if not catalog.layers[n].optional],
            "optional": {name: want_var(name) for name in catalog.optional_layers()},
            "layers": {
                name: {
                    # What selecting it does. Neither listing said, so the one thing a
                    # reader needs in order to choose was the one thing missing.
                    "purpose": LAYER_PURPOSE.get(name, ""),
                    "optional": layer.optional,
                    "has_tasks": layer.has_tasks,
                    "questions": {
                        q.name: {
                            "type": q.type,
                            "required": q.required,
                            "default": q.default,
                            "choices": q.choices,
                            "help": q.help,
                            # The exact string validate recognizes as "still a gap".
                            # Substituting another stand-in reports a clean answer set.
                            "placeholder": q.placeholder or None,
                            # Computed from other answers at render time. Do not ask
                            # for it, and do not pass the expression through.
                            "derived": q.derived,
                            "derived_from": q.derived_from or None,
                            # False means the interview does not ask it. Which of the
                            # next three is true says why, and what a caller should
                            # do about it.
                            "asked": q.asked,
                            # A tool version. Offer the one choice -- "set versions
                            # yourself, or take the pinned set?" -- and ask these
                            # only if the user says yes.
                            "pinned": q.pinned,
                            "pin_gate": PIN_GATE if q.pinned else None,
                            # A default that is already right. Same single choice,
                            # its own gate: "change the shipped defaults?"
                            "tuned": q.tuned,
                            "tune_gate": TUNE_GATE if q.tuned else None,
                            # A JSON artifact nobody types at a prompt. Not asked,
                            # and the one class a caller is expected to compose and
                            # supply -- unlike `derived`, which it must not.
                            "composed": q.composed,
                        }
                        for q in layer.questions.values()
                    },
                }
                for name, layer in catalog.layers.items()
            },
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print(f"{'layer':<14}{'select with':<22}{'q':>3}  purpose")
    print("-" * 78)
    for name in catalog.apply_order():
        layer = catalog.layers[name]
        selector = want_var(name) if layer.optional else "(always on)"
        print(f"{name:<14}{selector:<22}{len(layer.questions):>3}  {LAYER_PURPOSE.get(name, '')}")
    print("-" * 78)
    print(f"{len(catalog.layers)} layers, {len(catalog.all_question_names())} distinct questions")
    return 0


def not_asked_note(q: Question) -> str:
    """Why the interview would not ask for this, in three words.

    Four different reasons, and a reader acts on each differently: a pin and a tuned
    default are real questions behind one gate, a composed artifact is what a caller
    is expected to supply, and a derived value must never be passed through.
    """
    if q.pinned:
        return "  (tool version)"
    if q.tuned:
        return "  (shipped default)"
    if q.composed:
        return "  (composed)"
    return "" if q.asked else "  (derived)"


def cmd_presets(args: argparse.Namespace, catalog: Catalog) -> int:
    stacks = sorted(args.presets.glob("*.yml"))
    parts = sorted((args.presets / "parts").glob("*.yml"))

    if args.show:
        data, origin = load_preset(args.show, args.presets)
        if args.json:
            print(json.dumps({"answers": data, "from": origin}, indent=2, default=str))
            return 0
        print(f"{args.show} resolves to {len(data)} answers:\n")
        width = max((len(k) for k in data), default=0)
        questions = catalog.questions_for(selected_layers(catalog, data))
        notes = []
        for key in sorted(data):
            q = questions.get(key, Question(key))
            note = not_asked_note(q)
            if note:
                notes.append(key)
            print(f"  {key:<{width}}  {answer_display(data[key]):<46}  <- {origin.get(key)}{note}")
        print(f"\nlayers: {', '.join(selected_layers(catalog, data))}")
        if notes:
            print(
                f"{len(notes)} answer(s) the interview does not ask by default. A tool version "
                f"Renovate bumps, a default that is already right, an artifact composed from "
                f"the conversation, or a value derived from another answer. Each is still set "
                f"by this preset, by --set, and by a data file."
            )
        return 0

    if args.json:
        print(
            json.dumps(
                {
                    "stacks": {p.stem: load_preset(p.stem, args.presets)[0] for p in stacks},
                    "parts": {
                        f"parts/{p.stem}": load_preset(f"parts/{p.stem}", args.presets)[0]
                        for p in parts
                    },
                },
                indent=2,
                default=str,
            )
        )
        return 0

    if not stacks and not parts:
        print("no presets found")
        return 0

    print("STACKS - a whole project shape. Compose with --preset a --preset b.\n")
    for path in stacks:
        data, _ = load_preset(path.stem, args.presets)
        optional = [n for n in selected_layers(catalog, data) if catalog.layers[n].optional]
        print(f"  {path.stem:<20}{', '.join(optional) or '(always-on layers only)'}")

    if parts:
        print("\nPARTS - one concern each. Stacks are built from these.\n")
        for path in parts:
            data, _ = load_preset(f"parts/{path.stem}", args.presets)
            selects = sorted(
                k.removeprefix("WANT_").lower()
                for k, v in data.items()
                if k.startswith("WANT_") and v
            )
            print(f"  parts/{path.stem:<20}{', '.join(selects) or 'policy only'}")
    return 0


ANSWER_WIDTH = 46


def answer_display(value: object, width: int = ANSWER_WIDTH) -> str:
    """One line for one answer, honest about what it is not showing.

    A silent cut at a fixed column reads as the whole value: `mise install && go
    mod download && bun insta` looks like a command somebody could run, and a
    truncated JSON array looks like malformed JSON. Booleans print as YAML writes
    them, because these lines get copied into an answers file.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1] + "\u2026"


# gen_caller.py's own constant, duplicated for the same reason MEMBER_MANIFEST
# duplicates its languages: the check below has to match what gen_caller.py will
# refuse later, without importing an asset script that is not part of this package.
MEMBER_WORKFLOWS_DIR = ".github/workflows"


def _member_language_unsupported(
    catalog: Catalog, dest: Path, data: dict, *, member: bool
) -> list[Problem]:
    """A member declaring a language the root has no reusable CI workflow for.

    Measured: `--member` apply into a go+ts-only monorepo, with a rust member,
    registered it into `.ci/members.json` and printed "registered ... in
    .ci/members.json" as if it had succeeded. The mismatch surfaced only on the
    next, unrelated root re-apply, as `gen_caller.py` refusing with a message that
    names `.ci/members.json` and nothing about `--member` at all -- by then, the
    step that actually caused it is long past. Checked here so the refusal lands
    where the mistake was made.
    """
    if not member:
        return []
    root = find_scaffold_root(dest)
    if root is None:
        return []  # MEMBER_NO_ROOT already covers this
    workflows = root / MEMBER_WORKFLOWS_DIR
    if not workflows.is_dir():
        return []  # no GitHub caller to check against, e.g. a GitLab-only root
    lint = {p.stem.removeprefix("wc-lint-") for p in workflows.glob("wc-lint-*.yml")}
    test = {p.stem.removeprefix("wc-test-") for p in workflows.glob("wc-test-*.yml")}
    have = lint & test
    capabilities = member_capabilities(member_layers(catalog, data))
    missing = sorted(lang for lang in capabilities if lang not in have)
    if not missing:
        return []
    return [
        Problem(
            "error",
            "MEMBER_LANGUAGE_UNSUPPORTED",
            f"{root} has no CI workflow for {', '.join(missing)}. Registering this "
            f"member would sit in .ci/members.json unbuilt until a later root apply "
            f"refuses, for a reason that no longer mentions this command. Re-apply "
            f"the root with lang-{missing[0]} selected first, or drop that layer "
            f"from this member.",
        )
    ]


def checkout_problems(
    catalog: Catalog, dest: Path, data: dict, *, member: bool = False
) -> list[Problem]:
    """Everything the destination contradicts, cheap checks first.

    `repo_conflicts` reads two files. The orphan scan dry-runs Copier once per
    deselected layer, so it runs only when the recorded answers say a layer was
    turned off -- which is never, for the greenfield case every preset takes.
    """
    problems = repo_conflicts(dest, data, member=member)
    problems += _member_language_unsupported(catalog, dest, data, member=member)
    if (dest / INCOMPLETE_FILE).is_file():
        # Nothing else tells a killed or failed apply from a finished one: every file
        # it got to is real, and a re-apply leaves the previous answers file in place.
        problems.append(
            Problem(
                "warning",
                "INTERRUPTED_APPLY",
                f"an earlier apply into {dest} did not finish, so the scaffold there is "
                f"incomplete. Run apply again to finish it; every step is safe to repeat.",
                INCOMPLETE_FILE,
            )
        )
    dropped = deselected_layers(catalog, dest, data)
    if not dropped:
        return problems
    for layer, files in orphaned_files(catalog, dest, dropped, data).items():
        shown = ", ".join(files[:4]) + (f", and {len(files) - 4} more" if len(files) > 4 else "")
        problems.append(
            Problem(
                "warning",
                "STALE_LAYER_FILES",
                f"the {layer} layer is no longer selected, but {len(files)} file(s) it wrote "
                f"are still in {dest}: {shown}. Deselecting a layer stops it being written, "
                f"it does not remove what an earlier apply wrote -- and gen_caller.py and "
                f"gen_justfile.py wire CI and the justfile from which files are on disk, not "
                f"from this answer, so the layer keeps being built, linted and tested until "
                f"they are gone. Delete them to finish dropping the layer.",
                want_var(layer),
            )
        )
    return problems


def cmd_validate(args: argparse.Namespace, catalog: Catalog) -> int:
    data = load_data(args, catalog)
    member = getattr(args, "member", False)
    layers = member_layers(catalog, data) if member else selected_layers(catalog, data)
    problems = validate_data(catalog, data, layers=layers) + checkout_problems(
        catalog, Path(args.dest), data, member=member
    )
    errors = [p for p in problems if p.level == "error"]

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "layers": layers,
                    "answered": sorted(data),
                    "problems": [
                        {"level": p.level, "code": p.code, "key": p.key, "message": p.message}
                        for p in problems
                    ],
                },
                indent=2,
            )
        )
        return 1 if errors else 0

    print(f"layers:   {', '.join(layers)}")
    print(f"answered: {len(data)} keys")
    # Errors first. Grouped by check, the one line that decides whether anything gets
    # written arrived after two placeholder warnings nobody has to act on.
    for p in sorted(problems, key=lambda p: p.level != "error"):
        print(f"  {p}")
    if errors:
        print(f"\n{len(errors)} error(s). Nothing would be written.")
        return 1
    print("\nOK: every required question for the selected layers is answered.")
    return 0


def preserve_unasked(answers_file: Path, supplied: dict) -> list[str]:
    """Put back any supplied answer Copier did not record, and name them.

    Copier records only the questions it asked, so a question the interview
    deliberately skips -- a pinned tool version, a derived value -- vanishes from the
    answers file even when a preset or `--set` supplied it. An answers file that
    silently drops what you handed it does not reproduce the scaffold, which is the
    one thing it exists to do.
    """
    recorded = yaml.safe_load(answers_file.read_text()) or {}
    dropped = {k: v for k, v in supplied.items() if k not in recorded}
    if dropped:
        answers_file.write_text(
            answers_file.read_text().rstrip("\n")
            + "\n\n# Supplied but not asked: kept so this file reproduces the scaffold.\n"
            + yaml.safe_dump(dropped, sort_keys=True)
        )
    return sorted(dropped)


def still_relevant(catalog: Catalog, defaults: dict, answers_file: Path) -> dict:
    """The pre-filled defaults that still belong to a selected layer.

    A preset's answer for a layer the user then deselected was never asked, so
    keeping it would record `RUST_LIBRARY` in a repository with no Rust. A key that is
    no question at all is kept, so `validate` can name it rather than it vanishing.
    """
    recorded = yaml.safe_load(answers_file.read_text()) or {}
    chosen = catalog.questions_for(selected_layers(catalog, recorded))
    every = catalog.questions_for(list(catalog.layers))
    return {k: v for k, v in defaults.items() if k in chosen or k not in every}


def drop_copier_bookkeeping(answers_file: Path) -> None:
    """Remove Copier's own `_`-prefixed keys from the recorded answers.

    `_src_path` is an absolute path on the machine that ran the interview --
    `/Users/someone/dev/project-setup/templates/_interview` -- and this file is meant
    to be committed. Copier writes it for `copier update`, which this tool does not
    have; `load_data` drops every `_` key on the way back in, so it was never read
    either. What was left was one machine's home directory in somebody's repository.
    """
    recorded = yaml.safe_load(answers_file.read_text()) or {}
    kept = {k: v for k, v in recorded.items() if not k.startswith("_")}
    if len(kept) == len(recorded):
        return
    header = answers_file.read_text().partition("\n")[0]
    answers_file.write_text(header + "\n" + yaml.safe_dump(kept, sort_keys=True))


def unusable_dest(dest: Path) -> str | None:
    """Why this destination cannot hold a repository, in a sentence.

    `mkdir` raises FileExistsError for a file and NotADirectoryError for a path
    under one, and Copier's traceback names pathlib internals rather than the
    argument the user typed.
    """
    if dest.is_symlink() and not dest.is_dir():
        return f"--dest {dest} is a symlink to something that is not a directory"
    if dest.exists() and not dest.is_dir():
        return f"--dest {dest} is a file, not a directory"
    for parent in dest.parents:
        if parent.exists():
            if not parent.is_dir():
                return f"--dest {dest} sits under {parent}, which is a file"
            break
    return None


def seed_selection(catalog: Catalog, supplied: dict) -> dict:
    """Turn supplied layer answers into the multiselect's pre-selection.

    A preset names its layers one `WANT_<LAYER>: true` at a time; the interview asks
    once, with a list. Handing Copier both would make the booleans win -- supplied
    data beats a rendered default -- so the multiselect would show the preset's
    layers and then ignore every deselection the user made. The list is the single
    authority during an interview, and the booleans are derived back from it.
    """
    if not supplied:
        return supplied
    seeded = {k: v for k, v in supplied.items() if not k.startswith("WANT_")}
    chosen = [n for n in selected_layers(catalog, supplied) if catalog.layers[n].optional]
    if chosen or SELECTION in supplied:
        seeded[SELECTION] = sorted(set(chosen) | set(supplied.get(SELECTION) or []))
    return seeded


def interview_answers(args: argparse.Namespace, catalog: Catalog, dest: Path) -> tuple[dict, dict]:
    """Split what the interview was handed into (pre-filled defaults, settled answers).

    A preset, a data file and the destination's own previous answers are where the
    conversation starts, not answers to skip: handed to Copier as `data`, every one of
    them was treated as answered, so `--preset rust-cli` never showed the layer
    selection at all and lang-rust could not be deselected. They go in as
    `user_defaults`, which Copier pre-fills and still asks. Only `--set` settles a
    question -- except a layer choice, which is always a pre-selection, because
    settling one would skip the whole multiselect.

    A derived value is dropped from the defaults: it is a function of other answers,
    and a recorded one would pin whatever they were last time.
    """
    previous: dict = {}
    recorded = dest / ANSWERS_FILE
    if recorded.is_file():
        try:
            loaded = yaml.safe_load(recorded.read_text())
        except yaml.YAMLError:
            loaded = None
        if isinstance(loaded, dict):
            previous = {k: v for k, v in loaded.items() if not str(k).startswith("_")}
    starting = argparse.Namespace(**{**vars(args), "set": None})
    defaults = {**previous, **load_data(starting, catalog)}
    settling = argparse.Namespace(preset=None, data_file=None, set=args.set)
    answered = load_data(settling, catalog)
    for key in [k for k in answered if k.startswith("WANT_") or k == SELECTION]:
        defaults[key] = answered.pop(key)
    derived = {q.name for q in catalog.questions_for(list(catalog.layers)).values() if q.derived}
    defaults = {k: v for k, v in defaults.items() if k not in derived}
    return seed_selection(catalog, defaults), answered


def cmd_interview(args: argparse.Namespace, catalog: Catalog) -> int:
    """Hand the questions to Copier's own prompt engine. No LLM involved."""
    dest = Path(args.dest)
    if (problem := unusable_dest(dest)) is not None:
        print(problem, file=sys.stderr)
        return 1
    created = not dest.exists()
    dest.mkdir(parents=True, exist_ok=True)
    defaults, answered = interview_answers(args, catalog, dest)
    try:
        copier.run_copy(
            str(catalog.interview_path),
            dest,
            data=answered or None,
            user_defaults=defaults or None,
            overwrite=True,
            unsafe=False,
        )
    except (KeyboardInterrupt, CopierAnswersInterrupt, EOFError):
        return _interview_stopped(dest, created, "interrupted")
    except InteractiveSessionError:
        # What Copier raises on end of input: a closed stdin, or Ctrl-D at a prompt.
        return _interview_stopped(dest, created, "no more input")
    written = dest / ANSWERS_FILE
    if not written.is_file():
        return 1

    drop_copier_bookkeeping(written)
    kept = preserve_unasked(written, {**still_relevant(catalog, defaults, written), **answered})
    print(f"\nanswers: {written}")
    if kept:
        print(f"kept {len(kept)} supplied answer(s) the interview does not ask: {', '.join(kept)}")
    print(next_commands(written, args.dest))
    return 0


def _interview_stopped(dest: Path, created: bool, why: str) -> int:
    """Stop cleanly: a traceback ending in CopierAnswersInterrupt told nobody anything."""
    if created and not any(dest.iterdir()):
        dest.rmdir()
    print(
        f"\ninterview stopped ({why}); nothing was written. Run it again to start over.",
        file=sys.stderr,
    )
    return 130


def next_commands(answers_file: Path, dest: str) -> str:
    """The rest of the pipeline, with every argument filled in.

    Copier's own closing message cannot name the destination, and the command it
    printed without one -- `apply --data-file .project-setup-answers.yml` -- reads
    the answers file of whatever directory the user is standing in and scaffolds
    there, because `apply` defaults `--dest` to the working directory. Quoted,
    because a destination with a space in it is one argument.
    """
    data = shlex.quote(str(answers_file))
    where = shlex.quote(dest)
    return "\nnext:\n" + "\n".join(
        f"  project-setup {verb:<8} --data-file {data} --dest {where}"
        for verb in ("validate", "plan", "apply")
    )


def _files_report(result, changes: Changes) -> None:
    """What a brownfield user actually needs: which of their files lose something.

    Copier overwrites by default, so a plan is the only warning before it happens.
    Creations are counted; every existing file that changes is named, split by
    whether its owner loses lines, because that is the decision to make before
    applying. Both lists come from the difference a real run made -- plan's is a
    rehearsal in a copy -- so there is no disposition here to be wrong about.
    """
    would = result.pretend
    counts = [
        f"{len(changes.create)} file(s) {'to create' if would else 'created'}",
        f"{len(changes.overwrite)} {'to overwrite' if would else 'overwritten'}",
        f"{len(changes.merge)} {'to merge into' if would else 'merged into'}",
    ]
    if changes.remove:
        counts.append(f"{len(changes.remove)} {'to remove' if would else 'removed'}")
    print(", ".join(counts))

    def listing(paths: list[str], heading: str, note) -> None:
        if not paths:
            return
        width = max(len(p) for p in paths)
        print(f"\n{len(paths)} existing file(s) {heading}:")
        for path in paths:
            print(f"  {path:<{width}}  {note(path)}".rstrip())

    def link_note(path: str) -> str:
        return f"now a link to {changes.links[path]}" if path in changes.links else ""

    listing(
        changes.overwrite,
        "would be overwritten" if would else "overwritten",
        lambda p: " ".join(
            part for part in (f"{changes.lost[p]} line(s) of yours not kept", link_note(p)) if part
        ),
    )
    listing(
        changes.merge,
        ("would be merged into" if would else "merged into") + ", every line of yours kept",
        link_note,
    )
    listing(changes.remove, "would be removed" if would else "removed", lambda p: "")
    if would and (changes.overwrite or changes.remove):
        print("\n  Commit or move anything you want to keep before applying.")


def _register_member_if_applicable(dest: Path, result) -> str:
    """Record a successful member-scoped apply in the root it belongs to.

    `_refuse_checkout` already guaranteed a root exists above `dest` before apply
    ran, so this only has to name the member: gen_caller.py needs a language to
    build a job around, so a member with no `lang-*` layer is placed but left
    unregistered, and said so, rather than writing a capabilities object it would
    then refuse to read.
    """
    root = find_scaffold_root(dest)
    assert root is not None  # _refuse_checkout already required this
    capabilities = member_capabilities(result.layers)
    if not capabilities:
        return (
            f"not registered in {root}: none of {', '.join(result.layers) or '(no layers)'} "
            f"is a language layer, so there is no CI job capability to describe. Add one "
            f"with --set WANT_LANG_<X>=true, or register {dest} in {root / MEMBERS_JSON} "
            f"by hand."
        )
    return register_member(
        root,
        dest.resolve().name,
        dest.resolve().relative_to(root.resolve()).as_posix(),
        capabilities,
    )


def _report(
    result, changes: Changes, *, json_out: bool, verb: str, member_note: str | None = None
) -> int:
    if json_out:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "pretend": result.pretend,
                    "dest": str(result.dest),
                    "layers": result.layers,
                    "member": member_note,
                    "placed": [
                        {
                            "layer": s.name,
                            "ok": s.ok,
                            "detail": s.detail,
                            "warnings": s.warnings,
                            "seconds": round(s.seconds, 3),
                        }
                        for s in result.placed
                    ],
                    # What the run did to the destination, from the difference it made,
                    # not from what each step was expected to do. `overwrite` loses
                    # lines (`lines_lost` counts them); `merge` keeps every one.
                    "files": changes.as_json(),
                    "generated": [
                        {
                            "script": s.name,
                            "ok": s.ok,
                            "detail": s.detail,
                            "warnings": s.warnings,
                            "seconds": round(s.seconds, 3),
                        }
                        for s in result.generated
                    ],
                    # A step that degraded instead of failing. `ok` stays true, because
                    # a missing toolchain is not a broken scaffold -- but a caller that
                    # only reads `ok` reported a clean Rust project with no Cargo.toml.
                    "warnings": [
                        {"step": step, "message": message} for step, message in result.warnings
                    ],
                    "seconds": round(result.seconds, 3),
                },
                indent=2,
            )
        )
        return 0 if result.ok else 1

    print(f"{verb} {len(result.layers)} layers -> {result.dest}")
    for s in result.placed:
        mark = "ok  " if s.ok else "FAIL"
        print(f"  place    {mark} {s.name:<14} {s.seconds:.2f}s")
        if not s.ok:
            print(f"           {s.detail}")
    for s in result.generated:
        mark = "ok  " if s.ok else "FAIL"
        lines = s.detail.splitlines() or [""]
        print(f"  generate {mark} {s.name:<28} {s.seconds:.2f}s  {lines[0]}")
        # A generator that replaced something says so on a later line. Showing only
        # the first hides exactly the part worth reading.
        for line in lines[1:]:
            print(f"           {line.strip()}")
    print(f"total {result.seconds:.2f}s")
    _files_report(result, changes)
    if member_note:
        print(f"\n{member_note}")
    if not result.ok:
        print(
            f"\n{'apply would stop' if result.pretend else 'apply stopped'} at the step "
            f"marked FAIL above, leaving the scaffold incomplete. Fix it and "
            f"{'plan again' if result.pretend else 're-run apply'}; every step is safe to repeat."
        )
    return 0 if result.ok else 1


def _refuse_incomplete(catalog: Catalog, data: dict, verb: str, *, member: bool = False) -> bool:
    layers = member_layers(catalog, data) if member else None
    problems = [p for p in validate_data(catalog, data, layers=layers) if p.level == "error"]
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    if problems:
        print(f"refusing to {verb} against an incomplete answer set", file=sys.stderr)
    return bool(problems)


def _refuse_checkout(catalog: Catalog, dest: Path, data: dict, *, member: bool, verb: str) -> bool:
    """Print every checkout conflict, and say whether any of them is a refusal.

    Every conflict before NESTED_SCAFFOLD and MEMBER_NO_ROOT existed is a warning
    printed and continued past. Those two are errors: nesting a repository or
    scaffolding a member with nowhere to register it is not a warning.
    """
    problems = checkout_problems(catalog, dest, data, member=member)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    errors = [p for p in problems if p.level == "error"]
    if errors:
        print(f"refusing to {verb} {dest}", file=sys.stderr)
    return bool(errors)


def cmd_plan(args: argparse.Namespace, catalog: Catalog) -> int:
    """Rehearse the whole apply in a copy of the destination and report the difference.

    Tasks and generators run too, so the plan is what apply does rather than a
    prediction of it: two predictions were caught lying by hand before this.
    """
    data = load_data(args, catalog)
    member = args.member
    if _refuse_incomplete(catalog, data, "plan", member=member):
        return 1
    dest = Path(args.dest)
    if (problem := unusable_dest(dest)) is not None:
        print(problem, file=sys.stderr)
        return 1
    if _refuse_checkout(catalog, dest, data, member=member, verb="plan"):
        return 1
    layers = member_layers(catalog, data) if member else None
    result, changes = rehearse(catalog, dest, data, layers=layers)
    code = _report(result, changes, json_out=args.json, verb="would place")
    if not args.json:
        _warnings_report(result)
    return code


def cmd_apply(args: argparse.Namespace, catalog: Catalog) -> int:
    data = load_data(args, catalog)
    member = args.member
    if _refuse_incomplete(catalog, data, "apply", member=member):
        return 1
    dest = Path(args.dest)
    if (problem := unusable_dest(dest)) is not None:
        print(problem, file=sys.stderr)
        return 1
    if _refuse_checkout(catalog, dest, data, member=member, verb="apply"):
        return 1
    layers = member_layers(catalog, data) if member else None
    before = snapshot(dest)
    try:
        result = scaffold(
            catalog,
            dest,
            data,
            keep_dirs=before.dirs,
            run_tasks=not args.no_tasks,
            quiet=args.json,
            layers=layers,
        )
    except KeyboardInterrupt:
        print(
            f"\ninterrupted: {dest} is partly scaffolded. Re-run the same apply to "
            f"finish it; every step is safe to repeat.",
            file=sys.stderr,
        )
        return 130
    member_note = _register_member_if_applicable(dest, result) if member and result.ok else None
    code = _report(
        result,
        classify(before, snapshot(dest)),
        json_out=args.json,
        verb="placed",
        member_note=member_note,
    )

    # Last, so these are what is left on screen: what still needs a real value, and
    # what the run skipped. A placeholder is a reminder; a skipped `cargo init` means
    # the manifest is not there, so it goes after.
    if result.ok and not args.json:
        questions = catalog.questions_for(result.layers)
        holders = [
            (p.key, data.get(p.key) or questions[p.key].placeholder)
            for p in validate_data(catalog, data, layers=result.layers)
            if p.code == "PLACEHOLDER_IN_USE"
        ]
        if holders:
            width = max(len(k) for k, _ in holders)
            print(f"\n{len(holders)} answer(s) still carry a placeholder:")
            for key, value in holders:
                print(f"  {key:<{width}}  {value}")
            print("  Replace them, then re-run apply with the real values.")
        _warnings_report(result)
    return code


def _warnings_report(result) -> None:
    """Every step that degraded instead of failing.

    `apply` reported `place ok lang-rust`, `95 file(s) created` and exit 0 for a Rust
    repository with no Cargo.toml and no src/: `cargo` was absent, the task skipped as
    it is designed to, and the skip went into a captured log nobody printed. Degrading
    is the right trade -- a scaffold must not hard-fail on a missing toolchain -- so
    the exit code stays 0 and the report says what did not happen.
    """
    degraded = result.warnings
    if not degraded:
        return
    width = max(len(step) for step, _ in degraded)
    print(f"\n{len(degraded)} step(s) did less than the full job:")
    for step, message in degraded:
        print(f"  {step:<{width}}  {message}")


# --------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="project-setup", description=__doc__)
    p.add_argument(
        "--templates",
        type=Path,
        default=None,
        help=f"template layer directory (or {ENV_TEMPLATES})",
    )
    p.add_argument(
        "--presets",
        type=Path,
        default=None,
        help=f"preset directory (or {ENV_PRESETS})",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_data_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--preset",
            action="append",
            help="preset name; repeatable, later presets win",
        )
        sp.add_argument("--data-file", help="YAML answer file")
        sp.add_argument("--set", action="append", metavar="KEY=VALUE", help="override one answer")

    def add_member_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--member",
            action="store_true",
            help=(
                "this destination is a monorepo member, not a root: drop the root-only "
                "layers (git init, LICENSE, CI, ...) and register the result in the "
                "existing root above --dest"
            ),
        )

    c = sub.add_parser("catalog", help="list layers and the questions they declare")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_catalog)

    pr = sub.add_parser("presets", help="list or show standard project shapes")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--show", metavar="NAME")
    pr.set_defaults(func=cmd_presets)

    v = sub.add_parser("validate", help="check an answer set before writing anything")
    add_data_args(v)
    v.add_argument("--dest", default=".", help="checkout the answers are checked against")
    v.add_argument("--json", action="store_true")
    add_member_arg(v)
    v.set_defaults(func=cmd_validate)

    i = sub.add_parser("interview", help="ask the questions (Copier prompts, no LLM)")
    add_data_args(i)
    i.add_argument("--dest", default=".")
    i.set_defaults(func=cmd_interview)

    pl = sub.add_parser("plan", help="dry run: show what would be written")
    add_data_args(pl)
    pl.add_argument("--dest", default=".")
    pl.add_argument("--json", action="store_true")
    add_member_arg(pl)
    pl.set_defaults(func=cmd_plan)

    ap = sub.add_parser("apply", help="scaffold for real")
    add_data_args(ap)
    ap.add_argument("--dest", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-tasks", action="store_true", help="skip Copier _tasks")
    add_member_arg(ap)
    ap.set_defaults(func=cmd_apply)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.templates = resolve_data_dir("templates", args.templates)
    args.presets = resolve_data_dir("presets", args.presets)
    try:
        catalog = load_catalog(args.templates)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return args.func(args, catalog)


if __name__ == "__main__":
    raise SystemExit(main())
