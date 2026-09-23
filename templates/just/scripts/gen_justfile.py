#!/usr/bin/env python3
"""Rebuild the root justfile's import block from .just.d/.

    gen_justfile.py <dest>

just has no glob import, so one line per fragment has to be written. Every line is
`import?`, the optional form: a hard `import` of a missing file is a parse error
that breaks every recipe in the file, including the ones that would still work.

Only the block between the two markers is rewritten. Anything outside them survives,
so a hand-written recipe in the root justfile is not lost. A justfile with no markers
is somebody else's: the block is appended to it rather than refused, because the
`just` layer leaves an existing justfile in place and this is what wires its
fragments in.

Deterministic: fragments sort by name, so two runs produce the same bytes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BEGIN = "# BEGIN GENERATED: imports"
END = "# END GENERATED: imports"

# A recipe name at the start of a line, with any parameters after it.
RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+[^:]*)?:", re.M)

PREAMBLE = f"""\
{BEGIN}
# One line per .just.d fragment, rebuilt by `just just-sync`. Do not edit by hand.
#
# `import?` rather than `import`: the optional form. A missing file under the hard
# form is a parse error that takes down every recipe in the justfile, so a fragment
# removed by hand would break `just` entirely rather than just its own recipes.
"""


def fragments(dest: Path) -> list[Path]:
    directory = dest / ".just.d"
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.just") if p.is_file())


def check_collisions(found: list[Path]) -> None:
    """Refuse a fragment set that redefines a recipe name.

    Every fragment shares one flat namespace. A name defined twice is a hard error
    from just, and it breaks every recipe in the justfile rather than only the pair,
    so failing here names the two fragments instead.
    """
    owners: dict[str, str] = {}
    clashes = []
    for fragment in found:
        for name in RECIPE.findall(fragment.read_text()):
            if name in owners:
                clashes.append(f"{name!r} in both {owners[name]} and {fragment.name}")
            owners[name] = fragment.name
    if clashes:
        raise SystemExit("colliding recipe names: " + "; ".join(clashes))


def block(dest: Path) -> str:
    lines = [PREAMBLE]
    found = fragments(dest)
    check_collisions(found)
    if found:
        lines.extend(f"import? '.just.d/{path.name}'\n" for path in found)
    else:
        lines.append("# No fragments rendered yet.\n")
    lines.append(f"{END}\n")
    return "".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    dest = Path(sys.argv[1])
    justfile = dest / "justfile"
    if not justfile.is_file():
        print("no justfile to update", file=sys.stderr)
        return 3

    body = justfile.read_text()
    count = len(fragments(dest))
    if BEGIN not in body or END not in body:
        # A justfile this script did not write. The `just` layer no longer replaces an
        # existing one -- doing so deleted a repository's own recipes while `plan`
        # reported the file as "merged, your entries kept" -- so the import block has
        # to be added to whatever is there. The end of the file is the one safe place:
        # `import?` is position-independent in just, and everything already written
        # keeps its meaning.
        justfile.write_text(body.rstrip("\n") + "\n\n" + block(dest))
        print(
            f"justfile had no import block; appended one for {count} fragment(s), "
            "your recipes untouched"
        )
        return 0

    head, _, rest = body.partition(BEGIN)
    _, _, tail = rest.partition(END + "\n")
    justfile.write_text(head + block(dest) + tail)

    print(f"justfile imports {count} fragment(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
