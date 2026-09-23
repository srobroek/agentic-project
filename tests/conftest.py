"""Make `tools/` importable so the port's own invariant checks can be tested.

`tools/port_assets.py` is where the invariants that keep the question set usable are
enforced, so a test that pins one has to be able to call it. It is a script rather
than a package, and it is deliberately not installed: it runs from the checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
