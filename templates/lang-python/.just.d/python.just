# Python recipes, imported by the root justfile.

# Format, lint, type-check, and test
[group('python')]
python: python-fmt-check python-lint python-types python-test

# Format
[group('python')]
python-fmt:
    uv run ruff format .

# Report formatting without changing a file. `just check` runs this one, because
# a task called check must not rewrite the tree it is checking: it cannot then be
# run on a dirty checkout, and in CI it reports success having edited the files.
# `just python-fmt` is the writer.
[group('python')]
python-fmt-check:
    uv run ruff format --check .

# Lint
[group('python')]
python-lint:
    uv run ruff check .

# ruff has no per-rule severity, so `D` cannot sit in the config without gating a
# bare `ruff check`. `--exit-zero` keeps this one advisory.

# Docstring presence, advisory
[group('python')]
python-docs:
    uv run ruff check --select D --exit-zero .

# Type check
[group('python')]
python-types:
    uv run ty check

# Test
[group('python')]
python-test:
    uv run pytest

# Unused and undeclared dependencies
[group('python')]
python-deps:
    uv run deptry .

# Coverage
[group('python')]
python-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=lcov

# Test against every supported interpreter
[group('python')]
python-matrix:
    uv run nox

# `--frozen` fails rather than resolving when the lockfile and manifest disagree, so a
# stale lockfile is an error instead of a silent re-resolve. 43ms warm. The generator
# writes no lockfile, so the first setup creates it; every later run stays frozen.

# Install the locked dependencies into .venv
[group('python')]
python-install:
    @test -f uv.lock || uv lock
    uv sync --frozen
