"""Where the CLI looks for its templates.

The templates belong to the plugin, not the CLI, so the CLI has to be told where they
are. Getting this wrong means scaffolding from the wrong layer set, or a plain install
that cannot run at all -- both of which happened before these tests existed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_setup import cli
from project_setup.cli import ENV_PRESETS, ENV_TEMPLATES, resolve_data_dir


@pytest.fixture(autouse=True)
def _forget_the_installed_plugin():
    """`installed_plugin` asks OMP once per process; a faked answer must not leak."""
    cli.installed_plugin.cache_clear()
    yield
    cli.installed_plugin.cache_clear()


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


def test_failure_names_every_way_to_fix_it(monkeypatch, tmp_path: Path):
    """The error is the only thing standing between a user and a wrong guess."""
    fake_omp(monkeypatch, tmp_path, [])
    monkeypatch.setattr("project_setup.cli.REPO_ROOT", Path("/nonexistent"))
    with pytest.raises(SystemExit) as exc:
        resolve_data_dir("templates", None)
    message = str(exc.value)
    assert "--templates" in message
    assert ENV_TEMPLATES in message
    assert "OMP plugin @srobroek/project-setup" in message


def fake_omp(monkeypatch, tmp_path: Path, entries: list[dict]) -> None:
    """An `omp` on PATH that lists these npm plugins, and no source checkout."""
    import json
    import stat

    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps({"npm": entries, "marketplace": []}))
    omp = tmp_path / "bin" / "omp"
    omp.parent.mkdir()
    omp.write_text(f"#!/bin/sh\n/bin/cat {listing}\n")
    omp.chmod(omp.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(omp.parent))
    monkeypatch.delenv(ENV_TEMPLATES, raising=False)
    monkeypatch.delenv(ENV_PRESETS, raising=False)
    monkeypatch.setattr("project_setup.cli.REPO_ROOT", tmp_path / "not-a-checkout")
    cli.installed_plugin.cache_clear()


def plugin_dir(tmp_path: Path, name: str, version: str = cli.__version__) -> Path:
    root = tmp_path / name
    (root / "templates").mkdir(parents=True)
    (root / "presets").mkdir()
    (root / "package.json").write_text(f'{{"version": "{version}"}}')
    return root


def test_an_installed_plugin_needs_no_flags(monkeypatch, tmp_path: Path):
    """Outside a checkout every command needed --templates and --presets, by hand."""
    root = plugin_dir(tmp_path, "installed")
    fake_omp(monkeypatch, tmp_path, [{"name": "@srobroek/project-setup", "path": str(root)}])
    assert resolve_data_dir("templates", None) == root / "templates"
    assert resolve_data_dir("presets", None) == root / "presets"


def test_a_plugin_that_only_shares_the_suffix_is_not_used(monkeypatch, tmp_path: Path):
    """`project-setup` is also an older, unrelated plugin; its layers are the wrong set."""
    other = plugin_dir(tmp_path, "older")
    fake_omp(monkeypatch, tmp_path, [{"name": "@someone/project-setup", "path": str(other)}])
    with pytest.raises(SystemExit) as refused:
        resolve_data_dir("templates", None)
    # The exception alone proves only that something exited. What matters is that the
    # wrong plugin's layers were not adopted, so its path must not appear in the refusal.
    assert str(other) not in str(refused.value), "the unrelated plugin was used anyway"
    assert "--templates" in str(refused.value), "the refusal must name how to fix it"


def test_a_disabled_plugin_is_not_used(monkeypatch, tmp_path: Path):
    root = plugin_dir(tmp_path, "installed")
    fake_omp(
        monkeypatch,
        tmp_path,
        [{"name": "@srobroek/project-setup", "path": str(root), "enabled": False}],
    )
    with pytest.raises(SystemExit) as refused:
        resolve_data_dir("templates", None)
    # Any SystemExit satisfies a bare raises, so assert the disabled plugin's layers were
    # not adopted rather than only that something exited.
    assert str(root) not in str(refused.value), "a disabled plugin was used anyway"


def test_a_plugin_newer_than_the_cli_is_named(monkeypatch, tmp_path: Path, capsys):
    """The CLI is installed once and the plugin upgrades on its own."""
    root = plugin_dir(tmp_path, "installed", version="9.9.9")
    fake_omp(monkeypatch, tmp_path, [{"name": "@srobroek/project-setup", "path": str(root)}])
    assert resolve_data_dir("templates", None) == root / "templates"
    err = capsys.readouterr().err
    assert "9.9.9" in err and "uv tool install --reinstall" in err


def test_the_checkout_still_wins_over_the_installed_plugin(monkeypatch, tmp_path: Path):
    root = plugin_dir(tmp_path, "installed")
    fake_omp(monkeypatch, tmp_path, [{"name": "@srobroek/project-setup", "path": str(root)}])
    monkeypatch.setattr("project_setup.cli.REPO_ROOT", REPO)
    assert resolve_data_dir("templates", None) == REPO / "templates"
