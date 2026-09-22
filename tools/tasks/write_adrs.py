#!/usr/bin/env python3
"""Copier task: write one ADR per entry in the ADRS answer.

    write_adrs.py <template-path> <adrs-json>

Copier renders a template file once. An ADR manifest is a list, so the loop lives
here instead: the layer ships ADR.md.template beside this script (excluded from the
output) and each entry becomes docs/adr/NNNN-kebab-title.md.

Numbering continues from whatever is already in docs/adr/, so a second run adds
rather than renumbering. An entry whose file already exists is left alone.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

FIELDS = (
    "title",
    "status",
    "date",
    "decision",
    "rationale",
    "alternatives",
    "consequences",
    "confirmation",
)
REQUIRED = ("title", "decision", "rationale", "consequences")


def slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out or "untitled"


def next_number(adr_dir: Path) -> int:
    highest = 0
    for path in adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md"):
        highest = max(highest, int(path.name[:4]))
    return highest + 1


def existing_slugs(adr_dir: Path) -> set[str]:
    """Slugs already recorded, at any number.

    Matching on the slug rather than the whole filename is what makes a re-apply a
    no-op: the number differs on every run, so a filename comparison would write a
    duplicate of every ADR each time.
    """
    return {path.stem.split("-", 1)[1] for path in adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    template_path, raw = Path(sys.argv[1]), sys.argv[2]

    try:
        entries = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as exc:
        print(f"write_adrs: ADRS is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not entries:
        print("write_adrs: no ADRs requested")
        return 0
    if not isinstance(entries, list):
        print("write_adrs: ADRS must be a JSON array of objects", file=sys.stderr)
        return 1
    if not template_path.is_file():
        print(f"write_adrs: template missing: {template_path}", file=sys.stderr)
        return 1

    body = template_path.read_text()
    adr_dir = Path("docs/adr")
    adr_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    number = next_number(adr_dir)
    seen = existing_slugs(adr_dir)
    written = skipped = 0

    for entry in entries:
        if not isinstance(entry, dict):
            print(f"write_adrs: entry is not an object: {entry!r}", file=sys.stderr)
            return 1
        missing = [f for f in REQUIRED if not entry.get(f)]
        if missing:
            print(
                f"write_adrs: ADR {entry.get('title', '<untitled>')!r} is missing {missing}. "
                f"An ADR without a decision and its rationale is not a record.",
                file=sys.stderr,
            )
            return 1

        values = {f: str(entry.get(f, "")) for f in FIELDS}
        values["status"] = values["status"] or "accepted"
        values["date"] = values["date"] or today
        values["alternatives"] = values["alternatives"] or "None recorded."
        values["confirmation"] = values["confirmation"] or "Not yet verified."

        stem = slug(values["title"])
        if stem in seen:
            skipped += 1
            continue
        target = adr_dir / f"{number:04d}-{stem}.md"

        rendered = body
        for field, value in values.items():
            rendered = rendered.replace(f"@@ADR_{field.upper()}@@", value)
        if "@@" in rendered:
            leftover = re.findall(r"@@[A-Z_]+@@", rendered)
            print(f"write_adrs: unresolved tokens {leftover} in {target}", file=sys.stderr)
            return 1
        target.write_text(rendered)
        print(f"write_adrs: wrote {target}")
        seen.add(stem)
        number += 1
        written += 1

    note = f", {skipped} already recorded" if skipped else ""
    print(f"write_adrs: {written} ADR(s) written{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
