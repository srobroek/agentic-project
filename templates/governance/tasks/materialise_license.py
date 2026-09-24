#!/usr/bin/env python3
"""Copier task: write LICENSE from one of the bundled SPDX texts.

    materialise_license.py <licenses-dir> <SPDX_ID>

The layer carries every supported licence in its own excluded `tasks/licenses/`, and
this copies the one selected into LICENSE. Offline: nothing is fetched.

The texts are never placed into the destination. They used to be, as `licenses/`,
and this task then deleted that directory -- which also deleted whatever the
repository already kept there, including a REUSE-style `LICENSES/` on a
case-insensitive filesystem, and `plan` listed four files to create that no apply
ever left behind.

`NONE` is a real answer: an unpublished repository states no licence, and the four
choices were otherwise four ways to publish one.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# The one SPDX_ID that is not a licence. `deny.toml` and the OpenAPI contract read the
# same answer, so both carry a block for it.
NONE = "NONE"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    pool, spdx = Path(sys.argv[1]), sys.argv[2]
    if spdx == NONE:
        # A deliberate answer, not a degradation: an internal repository states no
        # licence.
        print("materialise_license: SPDX_ID is NONE, so no LICENSE was written")
        return 0
    src = pool / f"{spdx}.txt"
    if not src.is_file():
        available = ", ".join(sorted(p.stem for p in pool.glob("*.txt"))) or "none"
        print(
            f"materialise_license: no bundled text for {spdx!r} in {pool}. Available: {available}",
            file=sys.stderr,
        )
        return 1
    dest = Path("LICENSE")
    if dest.exists():
        print("materialise_license: LICENSE already present, leaving it alone")
    else:
        shutil.copyfile(src, dest)
        print(f"materialise_license: wrote LICENSE from {spdx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
