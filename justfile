set unstable := true

# Install the toolchain and the package in editable mode.
setup:
    mise install
    uv pip install -e ".[dev]" || uv pip install -e .
    uv pip install pytest ruff

# Re-port the asset layers from the omp-plugins source of truth.
port ASSETS:
    python3 tools/port_assets.py "{{ ASSETS }}" templates

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
    ruff check src tools
    ruff format --check src tools
    pytest

test:
    pytest -v

# Full end-to-end check: drive the real CLI over every preset.
e2e:
    .venv/bin/python tools/e2e.py

# Link this directory into OMP for local development.
omp-link:
    omp plugin link .
    omp plugin doctor

# Prove the capabilities are addressable (needs a responsive machine).
omp-verify:
    omp -p 'read skill://project-setup and reply with its name only'
    omp -p 'read rule://project-setup-no-handcopy and reply with its first MUST line'
