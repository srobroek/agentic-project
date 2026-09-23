"""Fail collection with one readable line when the interpreter is the wrong one.

`pythonpath` in `pyproject.toml` puts `src` and `tools` on the path, so a fresh clone
needs no editable install. What it cannot supply is the runtime dependencies: a bare
`pytest` resolves to whichever interpreter owns that shim, and on this machine that one
has neither `copier` nor `jinja2`. Collection then failed five times over with
`ModuleNotFoundError`, which reads like a broken checkout rather than a wrong command.
"""

from __future__ import annotations

import importlib.util

import pytest

# Imported by the package under test, not by the tests directly, so a missing one
# surfaces as a collection error inside `project_setup` with no hint of the cause.
RUNTIME = ("copier", "jinja2", "yaml")


def pytest_configure(config: pytest.Config) -> None:
    missing = [name for name in RUNTIME if importlib.util.find_spec(name) is None]
    if not missing:
        return
    raise pytest.UsageError(
        f"this interpreter is missing {', '.join(missing)}, so the suite cannot run.\n"
        f"  Run it through the project's own interpreter:\n"
        f"    .venv/bin/python -m pytest\n"
        f"  or let just do it:\n"
        f"    just check\n"
        f"  A bare `pytest` is whichever shim owns that name, which is not this project's."
    )
