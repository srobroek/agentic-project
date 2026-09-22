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
