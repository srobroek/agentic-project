"""Where the CLI looks for its templates.

The templates belong to the plugin, not the CLI, so the CLI has to be told where they
are. Getting this wrong means scaffolding from the wrong layer set, or a plain install
that cannot run at all -- both of which happened before these tests existed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_setup.cli import ENV_PRESETS, ENV_TEMPLATES, resolve_data_dir

REPO = Path(__file__).resolve().parents[1]


def test_source_checkout_resolves_without_being_told(monkeypatch):
    monkeypatch.delenv(ENV_TEMPLATES, raising=False)
    assert resolve_data_dir("templates", None) == REPO / "templates"


def test_env_var_is_used_when_set(monkeypatch, tmp_path: Path):
    (tmp_path / "templates").mkdir()
    monkeypatch.setenv(ENV_TEMPLATES, str(tmp_path / "templates"))
    assert resolve_data_dir("templates", None) == tmp_path / "templates"


def test_explicit_flag_beats_the_env_var(monkeypatch, tmp_path: Path):
    flagged, envd = tmp_path / "flagged", tmp_path / "envd"
    flagged.mkdir()
    envd.mkdir()
    monkeypatch.setenv(ENV_TEMPLATES, str(envd))
    assert resolve_data_dir("templates", flagged) == flagged


def test_a_nonexistent_env_var_falls_through(monkeypatch):
    """A stale export must not shadow a working checkout."""
    monkeypatch.setenv(ENV_TEMPLATES, "/nonexistent/templates")
    assert resolve_data_dir("templates", None) == REPO / "templates"


def test_presets_use_their_own_env_var(monkeypatch, tmp_path: Path):
    (tmp_path / "presets").mkdir()
    monkeypatch.setenv(ENV_PRESETS, str(tmp_path / "presets"))
    assert resolve_data_dir("presets", None) == tmp_path / "presets"


def test_failure_names_every_way_to_fix_it(monkeypatch):
    """The error is the only thing standing between a user and a wrong guess."""
    monkeypatch.delenv(ENV_TEMPLATES, raising=False)
    monkeypatch.setattr("project_setup.cli.REPO_ROOT", Path("/nonexistent"))
    with pytest.raises(SystemExit) as exc:
        resolve_data_dir("templates", None)
    message = str(exc.value)
    assert "--templates" in message
    assert ENV_TEMPLATES in message
    assert "omp plugin list --json" in message
