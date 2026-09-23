#!/usr/bin/env python3
"""Copier task: turn the bundled SPDX texts into a single LICENSE file.

    materialise_license.py <SPDX_ID>

The layer ships every supported licence under licenses/. This selects one, writes
LICENSE, and removes the rest so the project carries exactly one licence. Offline:
nothing is fetched.

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
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    spdx = sys.argv[1]
    pool = Path("licenses")
    if not pool.is_dir():
        # WARNING is the token the scaffolder greps out of a task's captured output,
        # so a skip reaches the summary instead of a log nobody prints.
        print(
            "materialise_license: WARNING no licenses/ directory here, so no LICENSE "
            "was written. The governance layer ships the texts; re-run apply with it "
            "selected.",
            file=sys.stderr,
        )
        return 0
    if spdx == NONE:
        # A deliberate answer, not a degradation: an internal repository states no
        # licence. The pool still goes, because carrying four unused licence texts
        # into a repository that publishes under none of them is worse than noise.
        shutil.rmtree(pool)
        print("materialise_license: SPDX_ID is NONE, so no LICENSE was written")
        return 0
    src = pool / f"{spdx}.txt"
    if not src.is_file():
        available = ", ".join(sorted(p.stem for p in pool.glob("*.txt")))
        print(
            f"materialise_license: no bundled text for {spdx!r}. Available: {available}",
            file=sys.stderr,
        )
        return 1
    dest = Path("LICENSE")
    if dest.exists():
        print("materialise_license: LICENSE already present, leaving it alone")
    else:
        shutil.copyfile(src, dest)
        print(f"materialise_license: wrote LICENSE from {spdx}")
    shutil.rmtree(pool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
