"""project-setup: scaffold a repository from layered Copier templates.

Designed to be driven by a human or an agent with the same commands. Every
subcommand takes --json so an agent never has to parse prose.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import copier
import yaml

from .catalog import (
    ANSWERS_FILE,
    PIN_GATE,
    Catalog,
    Question,
    load_catalog,
    selected_layers,
    validate_data,
    want_var,
)
from .runner import REPORTED_OPERATIONS, place_layers, prune_empty_dirs, run_generators

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_TEMPLATES = "PROJECT_SETUP_TEMPLATES"
ENV_PRESETS = "PROJECT_SETUP_PRESETS"


def resolve_data_dir(kind: str, explicit: Path | None) -> Path:
    """Find the templates or presets directory.

    The templates are the plugin's payload, not the CLI's. They are deliberately not
    bundled into the wheel: the plugin can be upgraded on its own, and a bundled copy
    would go stale without saying so. So the CLI has to be told where they are, in
    order of precedence:

      1. --templates / --presets
      2. PROJECT_SETUP_TEMPLATES / PROJECT_SETUP_PRESETS
      3. a source checkout, when running from one

    Anything else is an error that names all three, because a wrong guess here means
    scaffolding from the wrong layer set.
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

    tried = "\n".join(f"    {origin}: {path}" for origin, path in candidates)
    raise SystemExit(
        f"error: no {kind} directory found. Tried:\n{tried}\n\n"
        f"  Point the CLI at the plugin's {kind}, either per run:\n"
        f"    project-setup --{kind} /path/to/plugin/{kind} ...\n"
        f"  or once, for the shell:\n"
        f"    export {env_var}=/path/to/plugin/{kind}\n\n"
        f"  An OMP-installed plugin reports its own path:\n"
        f"    omp plugin list --json"
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
                    "optional": layer.optional,
                    "has_tasks": layer.has_tasks,
                    "questions": {
                        q.name: {
                            "type": q.type,
                            "required": q.required,
                            "default": q.default,
                            "choices": q.choices,
                            "help": q.help,
                            # The exact string validate recognises as "still a gap".
                            # Substituting another stand-in reports a clean answer set.
                            "placeholder": q.placeholder or None,
                            # Computed from other answers at render time. Do not ask
                            # for it, and do not pass the expression through.
                            "derived": q.derived,
                            "derived_from": q.derived_from or None,
                            # False means there is nothing to ask: the value is
                            # derived. Still settable with --set.
                            "asked": q.asked,
                            # A tool version. Offer the one choice -- "set versions
                            # yourself, or take the pinned set?" -- and ask these
                            # only if the user says yes.
                            "pinned": q.pinned,
                            "pin_gate": PIN_GATE if q.pinned else None,
                        }
                        for q in layer.questions.values()
                    },
                }
                for name, layer in catalog.layers.items()
            },
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print(f"{'layer':<14}{'select with':<22}{'questions':>10}  tasks")
    print("-" * 60)
    for name in catalog.apply_order():
        layer = catalog.layers[name]
        selector = want_var(name) if layer.optional else "(always on)"
        print(
            f"{name:<14}{selector:<22}{len(layer.questions):>10}"
            f"  {'yes' if layer.has_tasks else '-'}"
        )
    print("-" * 60)
    print(f"{len(catalog.layers)} layers, {len(catalog.all_question_names())} distinct questions")
    return 0


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
            note = "  (tool version)" if q.pinned else "" if q.asked else "  (derived)"
            if note:
                notes.append(key)
            print(f"  {key:<{width}}  {answer_display(data[key]):<46}  <- {origin.get(key)}{note}")
        print(f"\nlayers: {', '.join(selected_layers(catalog, data))}")
        if notes:
            print(
                f"{len(notes)} answer(s) are not asked unless you ask to set them: a tool "
                f"version Renovate bumps, or a value derived from another answer."
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


def cmd_validate(args: argparse.Namespace, catalog: Catalog) -> int:
    data = load_data(args, catalog)
    problems = validate_data(catalog, data)
    layers = selected_layers(catalog, data)
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
    for p in problems:
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


def cmd_interview(args: argparse.Namespace, catalog: Catalog) -> int:
    """Hand the questions to Copier's own prompt engine. No LLM involved."""
    dest = Path(args.dest)
    if (problem := unusable_dest(dest)) is not None:
        print(problem, file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    supplied = load_data(args, catalog) if (args.preset or args.data_file or args.set) else {}
    copier.run_copy(
        str(catalog.interview_path),
        dest,
        data=supplied or None,
        overwrite=True,
        unsafe=False,
    )
    written = dest / ANSWERS_FILE
    if not written.is_file():
        return 1

    kept = preserve_unasked(written, supplied)
    print(f"\nanswers: {written}")
    if kept:
        print(f"kept {len(kept)} supplied answer(s) the interview does not ask: {', '.join(kept)}")
    return 0


def _files_report(result) -> None:
    """What a brownfield user actually needs: the list of their files being replaced.

    Copier overwrites by default, so a plan is the only warning before it happens.
    Creations are counted; replacements are named, every one, because each is a file
    somebody wrote by hand.
    """
    created, overwritten = result.files("create"), result.files("overwrite")
    noun = "file(s) to create" if result.pretend else "file(s) created"
    print(
        f"{len(created)} {noun}, {len(overwritten)} to overwrite"
        if result.pretend
        else f"{len(created)} {noun}"
    )
    if not overwritten:
        return
    verb = "would be overwritten" if result.pretend else "overwritten"
    print(f"\n{len(overwritten)} existing file(s) {verb}:")
    for path in overwritten:
        print(f"  {path}")
    if result.pretend:
        print("\n  Copier overwrites by default. Commit or move anything you want to keep.")


def _report(result, *, json_out: bool, verb: str) -> int:
    if json_out:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "pretend": result.pretend,
                    "dest": str(result.dest),
                    "layers": result.layers,
                    "placed": [
                        {
                            "layer": s.name,
                            "ok": s.ok,
                            "detail": s.detail,
                            "seconds": round(s.seconds, 3),
                        }
                        for s in result.placed
                    ],
                    # Named per operation so a caller can warn before overwriting.
                    "files": {
                        operation: result.files(operation) for operation in REPORTED_OPERATIONS
                    },
                    "generated": [
                        {
                            "script": s.name,
                            "ok": s.ok,
                            "detail": s.detail,
                            "seconds": round(s.seconds, 3),
                        }
                        for s in result.generated
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
    _files_report(result)
    return 0 if result.ok else 1


def cmd_plan(args: argparse.Namespace, catalog: Catalog) -> int:
    data = load_data(args, catalog)
    problems = [p for p in validate_data(catalog, data) if p.level == "error"]
    if problems:
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("refusing to plan against an incomplete answer set", file=sys.stderr)
        return 1
    result = place_layers(
        catalog,
        Path(args.dest),
        data,
        pretend=True,
        run_tasks=False,
        quiet=args.json,
    )
    return _report(result, json_out=args.json, verb="would place")


def cmd_apply(args: argparse.Namespace, catalog: Catalog) -> int:
    data = load_data(args, catalog)
    problems = [p for p in validate_data(catalog, data) if p.level == "error"]
    if problems:
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("refusing to apply against an incomplete answer set", file=sys.stderr)
        return 1
    dest = Path(args.dest)
    if (problem := unusable_dest(dest)) is not None:
        print(problem, file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    result = place_layers(
        catalog,
        dest,
        data,
        run_tasks=not args.no_tasks,
        quiet=args.json,
    )
    if result.ok:
        prune_empty_dirs(dest)
        run_generators(
            dest,
            result,
            # Copier applies a layer's defaults itself; a generator argument is
            # assembled out here and would otherwise miss every unanswered one.
            data={**catalog.defaults_for(result.layers), **data},
            quiet=True,
        )
        (dest / ANSWERS_FILE).write_text(
            "# Written by project-setup apply. Re-run with --data-file to reproduce.\n"
            + yaml.safe_dump(data, sort_keys=True)
        )
    code = _report(result, json_out=args.json, verb="placed")

    # Last, so it is the thing left on screen: what still needs a real value.
    if result.ok and not args.json:
        questions = catalog.questions_for(selected_layers(catalog, data))
        holders = [
            (p.key, data.get(p.key) or questions[p.key].placeholder)
            for p in validate_data(catalog, data)
            if p.code == "PLACEHOLDER_IN_USE"
        ]
        if holders:
            width = max(len(k) for k, _ in holders)
            print(f"\n{len(holders)} answer(s) still carry a placeholder:")
            for key, value in holders:
                print(f"  {key:<{width}}  {value}")
            print("  Replace them, then re-run apply with the real values.")
    return code


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

    c = sub.add_parser("catalog", help="list layers and the questions they declare")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_catalog)

    pr = sub.add_parser("presets", help="list or show standard project shapes")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--show", metavar="NAME")
    pr.set_defaults(func=cmd_presets)

    v = sub.add_parser("validate", help="check an answer set before writing anything")
    add_data_args(v)
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_validate)

    i = sub.add_parser("interview", help="ask the questions (Copier prompts, no LLM)")
    add_data_args(i)
    i.add_argument("--dest", default=".")
    i.set_defaults(func=cmd_interview)

    pl = sub.add_parser("plan", help="dry run: show what would be written")
    add_data_args(pl)
    pl.add_argument("--dest", default=".")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_plan)

    ap = sub.add_parser("apply", help="scaffold for real")
    add_data_args(ap)
    ap.add_argument("--dest", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-tasks", action="store_true", help="skip Copier _tasks")
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
