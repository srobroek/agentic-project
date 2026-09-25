"""Every tool a recipe invokes has to be provided by something.

The failure this guards against is silent until someone runs the recipe: `ts-fmt` called
`bunx biome` with biome in no manifest and no mise conf, so bun fell through to PATH and
a fresh scaffold died on "No version is set for shim: biome". `python-types` called
`uv run ty` with ty in no dependency group, and failed with "Failed to spawn: ty".
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

from project_setup.catalog import ALWAYS_ON

ASSETS = Path(__file__).resolve().parents[1] / "assets"

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

# A package manager resolves what follows it: `bunx biome` from node_modules,
# `uv run ty` from the dependency group, `cargo nextest` from the cargo bin dir.
RUNNERS = frozenset(
    {"bunx", "bun", "uv", "uvx", "cargo", "go", "npm", "npx", "pnpm", "mise", "git", "just"}
)

# Shell grammar and coreutils, which are not the toolchain's business.
SHELL = frozenset(
    {
        "awk",
        "case",
        "cat",
        "cd",
        "command",
        "curl",
        "cp",
        "do",
        "done",
        "echo",
        "elif",
        "else",
        "esac",
        "exit",
        "false",
        "fi",
        "find",
        "for",
        "grep",
        "head",
        "if",
        "mkdir",
        "mv",
        "printf",
        "python3",
        "read",
        "rm",
        "sed",
        "set",
        "sort",
        "tail",
        "test",
        "then",
        "trap",
        "true",
        "while",
    }
)

# Shipped by a pinned toolchain rather than pinned separately.
BUNDLED = {"gofmt": "go"}


def binary(tool: str) -> str:
    """mise names a tool `backend:owner/name`; the binary is the last segment."""
    return tool.split(":")[-1].rsplit("/", 1)[-1]


def pinned_tools(layer: str) -> set[str]:
    found: set[str] = set()
    for conf in (TEMPLATES / layer).glob(".mise/conf.d/*.toml*"):
        # A rendered version is still a valid TOML string once the delimiters go.
        table = tomllib.loads(conf.read_text().replace("@@", "x"))
        found |= {binary(name) for name in table.get("tools", {})}
    return found


def invoked_tools(layer: str) -> set[str]:
    called: set[str] = set()
    for fragment in (TEMPLATES / layer).glob(".just.d/*.just*"):
        for line in fragment.read_text().splitlines():
            if not line.startswith((" ", "\t")):
                continue
            words = line.strip().split()
            if not words:
                continue
            word = words[0].lstrip("@-")
            if word in RUNNERS or word in SHELL:
                continue
            if not word or not word[0].isalpha() or any(c in word for c in "=${}!["):
                continue
            called.add(word)
    return called


LAYERS = sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir() and not p.name.startswith("_"))


@pytest.mark.parametrize("layer", LAYERS)
def test_every_tool_a_recipe_calls_is_pinned_somewhere(layer: str):
    available = pinned_tools(layer)
    for name in ALWAYS_ON:
        available |= pinned_tools(name)
    available |= {tool for tool, host in BUNDLED.items() if host in available}

    missing = sorted(invoked_tools(layer) - available)
    assert missing == [], f"{layer} recipes call {missing}, which no .mise/conf.d/ pins"


# --------------------------------------------------------- check must not rewrite


LANGUAGES = ("ts", "go", "python", "rust")


def fragment(lang: str) -> str:
    """A language's just fragment, whether it carries a token or not."""
    base = ASSETS / f"lang/{lang}/.just.d/{lang}.just"
    return (base if base.is_file() else base.with_name(base.name + ".template")).read_text()


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_language_aggregate_checks_formatting_without_writing(lang):
    """`just check` runs each language's aggregate. A check that formats cannot be run
    on a dirty checkout, and in CI it reports success having edited the tree it was
    asked to inspect. The writer stays available as `just <lang>-fmt`.
    """
    body = fragment(lang)
    aggregate = next(line for line in body.splitlines() if line.startswith(f"{lang}:"))
    assert f"{lang}-fmt-check" in aggregate
    assert f" {lang}-fmt " not in f" {aggregate} "
    assert f"{lang}-fmt-check:" in body, "the check-only recipe must exist"
    assert f"{lang}-fmt:" in body, "the writer must remain available"


@pytest.mark.parametrize(
    "recipe,forbidden",
    [
        ("ts-fmt-check", "--write"),
        ("python-fmt-check", "ruff format ."),
        ("rust-fmt-check", "cargo fmt --all\n"),
        ("go-fmt-check", "gofmt -w"),
    ],
)
def test_no_check_recipe_carries_a_writing_flag(recipe, forbidden):
    lang = recipe.split("-")[0]
    body = fragment(lang)
    block = body.split(f"{recipe}:")[1].split("\n[group")[0]
    assert forbidden not in block, f"{recipe} still writes: {forbidden!r}"


def test_no_preset_restates_a_pinned_tool_version():
    """One source of truth for a version, so Renovate has one place to bump.

    GOLANGCI_LINT_VERSION was pinned in both TOKEN_POLICY and presets/parts/lang-go.yml.
    Bumping the policy alone changed nothing, because the preset wins, and a fresh
    go-service kept failing `just check` with golangci-lint built against an older Go
    than the pinned toolchain.
    """
    import re

    source = (Path(__file__).resolve().parents[1] / "tools" / "port_assets.py").read_text()
    pinned = set(re.findall(r'"([A-Z0-9_]+)": \{[^}]*"pin": True', source, re.S))
    assert pinned, "no pinned tokens found; the pattern this guards has moved"

    offenders: dict[str, list[str]] = {}
    for path in sorted((Path(__file__).resolve().parents[1] / "presets").rglob("*.yml")):
        answers = yaml.safe_load(path.read_text()) or {}
        restated = sorted(k for k in answers if k in pinned)
        if restated:
            offenders[path.name] = restated
    assert offenders == {}, f"version pins restated in a preset: {offenders}"


def test_the_a11y_data_is_not_inlined_into_typescript():
    """No fixed layout in a .ts file can satisfy a formatter for every answer.

    A JSON answer has quoted keys, which no JS formatter emits, and its rendered
    length decides whether the formatter wants the call on one line or three. Both
    a11y arrays therefore live in their own JSON files, read at run time, and are
    excluded from the formatter because their shape is nobody's to read.
    """
    playwright = ASSETS / "a11y/ts/playwright"
    assert (playwright / "surfaces.json.template").is_file()
    assert (playwright / "web-servers.json.template").is_file()
    for name in ("playwright.a11y.config.ts.template", "tests/a11y/a11y.pw.ts.template"):
        body = (playwright / name).read_text()
        assert "A11Y_SURFACES_JSON" not in body, f"{name} still inlines the answer"
        assert "A11Y_WEB_SERVERS_JSON" not in body, f"{name} still inlines the answer"
        assert "readFileSync" in body

    biome = (ASSETS / "lang/ts/biome.json.template").read_text()
    assert "!**/.a11y/surfaces.json" in biome
    assert "!**/.a11y/web-servers.json" in biome


def test_the_playwright_config_avoids_esm_only_apis():
    """Playwright loads a .ts config through a CJS transform and .a11y/package.json
    declares no module type, so import.meta.url failed with "exports is not defined in
    ES module scope" before any test ran."""
    body = (ASSETS / "a11y/ts/playwright/playwright.a11y.config.ts.template").read_text()
    assert "import.meta" not in body
    assert "node:url" not in body


def test_an_empty_surface_set_is_a_gap_not_a_failure():
    """A route nobody has written cannot be scanned, and a webServer command that does
    not exist failed `just check` on a fresh scaffold."""
    recipe = (ASSETS / "a11y/ts/playwright/.just.d/a11y.just.template").read_text()
    assert "--pass-with-no-tests" in recipe

    part = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "presets/parts/web-ui.yml").read_text()
    )
    assert part["A11Y_SURFACES_JSON"] == "[]"
    assert part["A11Y_WEB_SERVERS_JSON"] == "[]"


def test_playwright_run_artifacts_are_ignored():
    body = (ASSETS / "a11y/ts/playwright/.gitignore.d/a11y").read_text()
    assert "test-results" in body


def test_the_formatter_leaves_the_cdk_app_alone():
    """`cdk init` generates a standalone project with its own conventions. The root
    tsconfig already excludes it; the root formatter did not, so `just check` passed
    on a fresh scaffold and then failed the moment `just aws-cdk-init` ran.
    """
    biome = (ASSETS / "lang/ts/biome.json.template").read_text()
    assert "!**/@@AWS_CDK_DEST@@/**" in biome

    tsconfig = (ASSETS / "lang/ts/tsconfig.json.template").read_text()
    assert "@@AWS_CDK_DEST@@" in tsconfig, "the two exclusions must name the same answer"


# ------------------------------------------------------- setup installs this project


def test_setup_installs_only_this_projects_tools():
    """`mise install` with no scope installs every tool in scope, and the developer's
    global config is in scope: a minimal scaffold pulled 116 tools on a machine with a
    populated ~/.config/mise, against the 13 it declares. Pointing the global config at
    an empty file for the install alone leaves the rest of the environment untouched.
    """
    setup = (ASSETS / "just/justfile").read_text()
    block = setup.split("setup:")[1].split("\n# ")[0]
    assert "MISE_GLOBAL_CONFIG_FILE=" in block
    for line in block.splitlines():
        bare = line.strip()
        if bare.startswith("mise install"):
            raise AssertionError(f"unscoped install would take the global set: {bare!r}")


def test_this_repo_pins_its_own_prose_gate():
    """The slopvac on PATH was a python-install shadow that fails with
    ModuleNotFoundError: slopvac.cli, so the gate's verdict depended on which
    interpreter mise resolved in a given directory."""
    pinned = tomllib.loads((Path(__file__).resolve().parents[1] / "mise.toml").read_text())
    assert any("slopvac" in name for name in pinned["tools"]), (
        "slopvac unpinned, so a broken copy on PATH can answer for the gate"
    )


def test_the_vulnerability_check_names_its_own_workaround():
    """`go run <module>@<version>` downloads through GOPROXY, and on a network that
    cannot reach the proxy the failure is a bare timeout naming no fix. The hint is
    gated on the proxy really not answering, so a genuine vulnerability report never
    carries a network excuse.
    """
    recipe = (ASSETS / "lang/go/.just.d/go.just.template").read_text()
    assert "GOPROXY=direct just go-vuln" in recipe
    assert "go env GOPROXY" in recipe, "the hint must probe the configured proxy, not a guess"
    assert '[ "$status" -ne 0 ]' in recipe, "the hint must fire only on failure"
    assert "-ne 127" in recipe, "a missing curl must not be read as a dead proxy"
