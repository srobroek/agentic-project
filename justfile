set unstable := true

# Every recipe runs the project interpreter explicitly. `python3` resolves to the
# venv only in a shell where mise is active, which `just` cannot assume: the first
# documented command failing on a fresh clone is a bad first impression.
python := ".venv/bin/python"

# Install the toolchain and the package in editable mode.
setup:
    mise install
    uv pip install -e ".[dev]" || uv pip install -e .
    uv pip install pytest ruff

# Re-port the asset layers from the vendored source of truth.
port ASSETS="./assets":
    {{ python }} tools/port_assets.py "{{ ASSETS }}" templates

# List every template layer and the questions it declares.
catalog:
    project-setup catalog

# Show what a preset would write, without writing it.
plan PRESET DEST:
    project-setup plan --preset "{{ PRESET }}" --dest "{{ DEST }}"

# Scaffold for real.
apply PRESET DEST:
    project-setup apply --preset "{{ PRESET }}" --dest "{{ DEST }}"

check:
    {{ python }} -m ruff check src tools tests
    {{ python }} -m ruff format --check src tools tests
    {{ python }} -m pytest

test:
    {{ python }} -m pytest -v

# Full end-to-end check: drive the real CLI over every preset.
e2e:
    {{ python }} tools/e2e.py

# Link this directory into OMP for local development.
omp-link:
    omp plugin link .
    omp plugin doctor

# Prove the capabilities are addressable (needs a responsive machine).
omp-verify:
    omp -p 'read skill://project-setup and reply with its name only'
    omp -p 'read rule://project-setup-no-handcopy and reply with its first MUST line'
