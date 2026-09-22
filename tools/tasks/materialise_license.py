#!/usr/bin/env python3
"""Copier task: turn the bundled SPDX texts into a single LICENSE file.

    materialise_license.py <SPDX_ID>

The layer ships every supported licence under licenses/. This selects one, writes
LICENSE, and removes the rest so the project carries exactly one licence. Offline:
nothing is fetched.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    spdx = sys.argv[1]
    pool = Path("licenses")
    if not pool.is_dir():
        print("materialise_license: no licenses/ directory; skipping", file=sys.stderr)
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
