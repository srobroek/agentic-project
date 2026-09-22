"""project-setup: scaffold a repository from layered Copier templates.

Designed to be driven by a human or an agent with the same commands. Every
subcommand takes --json so an agent never has to parse prose.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import copier
import yaml

from .catalog import (
    ANSWERS_FILE,
    Catalog,
    load_catalog,
    selected_layers,
    validate_data,
    want_var,
)
from .runner import place_layers, prune_empty_dirs, run_generators

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATES = REPO_ROOT / "templates"
DEFAULT_PRESETS = REPO_ROOT / "presets"


# --------------------------------------------------------------- data loading


def load_data(args: argparse.Namespace) -> dict:
    """Build the answer set from a preset, a data file, and --set overrides.

    Later sources win, so an agent can copy a preset and override the few keys it
    needs rather than restating the whole thing.
    """
    data: dict = {}
    if getattr(args, "preset", None):
        path = resolve_preset(args.preset, args.presets)
        data.update(yaml.safe_load(path.read_text()) or {})
    if getattr(args, "data_file", None):
        path = Path(args.data_file)
        if not path.is_file():
            raise SystemExit(f"data file not found: {path}")
        data.update(yaml.safe_load(path.read_text()) or {})
    for pair in getattr(args, "set", None) or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects KEY=VALUE, got {pair!r}")
        key, _, raw = pair.partition("=")
        data[key.strip()] = parse_scalar(raw)
    # Copier records provenance keys in the answers file; they are not questions.
    return {k: v for k, v in data.items() if not k.startswith("_")}


def parse_scalar(raw: str) -> object:
    """Parse a --set value as YAML, falling back to the literal string.

    Values like `@me` or `security@example.com` are not valid bare YAML scalars,
    and a CLI override should never fail on that.
    """
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    return raw if parsed is None and raw != "" else parsed


def resolve_preset(name: str, presets_dir: Path) -> Path:
    for candidate in (presets_dir / name, presets_dir / f"{name}.yml"):
        if candidate.is_file():
            return candidate
    available = ", ".join(sorted(p.stem for p in presets_dir.glob("*.yml"))) or "none"
    raise SystemExit(f"unknown preset {name!r}. Available: {available}")


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
    files = sorted(args.presets.glob("*.yml"))
    if args.show:
        path = resolve_preset(args.show, args.presets)
        print(path.read_text(), end="")
        return 0
    if args.json:
        print(
            json.dumps(
                {p.stem: yaml.safe_load(p.read_text()) or {} for p in files},
                indent=2,
            )
        )
        return 0
    if not files:
        print("no presets found")
        return 0
    for path in files:
        data = yaml.safe_load(path.read_text()) or {}
        layers = selected_layers(catalog, data)
        summary = data.get("DESCRIPTION", "")
        print(f"{path.stem:<18}{', '.join(layers)}")
        if summary:
            print(f"{'':<16}{summary}")
    return 0


def cmd_validate(args: argparse.Namespace, catalog: Catalog) -> int:
    data = load_data(args)
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


def cmd_interview(args: argparse.Namespace, catalog: Catalog) -> int:
    """Hand the questions to Copier's own prompt engine. No LLM involved."""
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    preset = load_data(args) if (args.preset or args.data_file or args.set) else None
    copier.run_copy(
        str(catalog.interview_path),
        dest,
        data=preset,
        overwrite=True,
        unsafe=False,
    )
    written = dest / ANSWERS_FILE
    print(f"\nanswers: {written}")
    return 0 if written.is_file() else 1


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
        first = s.detail.splitlines()[0] if s.detail else ""
        print(f"  generate {mark} {s.name:<28} {s.seconds:.2f}s  {first}")
    print(f"total {result.seconds:.2f}s")
    return 0 if result.ok else 1


def cmd_plan(args: argparse.Namespace, catalog: Catalog) -> int:
    data = load_data(args)
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
        capture_stdout=args.json,
    )
    return _report(result, json_out=args.json, verb="would place")


def cmd_apply(args: argparse.Namespace, catalog: Catalog) -> int:
    data = load_data(args)
    problems = [p for p in validate_data(catalog, data) if p.level == "error"]
    if problems:
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("refusing to apply against an incomplete answer set", file=sys.stderr)
        return 1
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    result = place_layers(
        catalog,
        dest,
        data,
        run_tasks=not args.no_tasks,
        quiet=True,
        capture_stdout=args.json,
    )
    if result.ok:
        prune_empty_dirs(dest)
        run_generators(dest, result, data=data, quiet=True)
    if result.ok:
        (dest / ANSWERS_FILE).write_text(
            "# Written by project-setup apply. Re-run with --data-file to reproduce.\n"
            + yaml.safe_dump(data, sort_keys=True)
        )
    return _report(result, json_out=args.json, verb="placed")


# --------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="project-setup", description=__doc__)
    p.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES)
    p.add_argument("--presets", type=Path, default=DEFAULT_PRESETS)
    sub = p.add_subparsers(dest="command", required=True)

    def add_data_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--preset", help="preset name from presets/")
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
    try:
        catalog = load_catalog(args.templates)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return args.func(args, catalog)


if __name__ == "__main__":
    raise SystemExit(main())
