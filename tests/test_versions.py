"""Offline contract tests for the pinned toolchain versions.

The release checks that informed VERSION_COMPATIBILITY live beside TOKEN_POLICY in
``tools/port_assets.py``. These tests deliberately inspect source assets rather than
calling a registry, so a fresh checkout can run the gate without network access.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
POLICY = runpy.run_path(str(ROOT / "tools" / "port_assets.py"))
TOKEN_POLICY: dict[str, dict] = POLICY["TOKEN_POLICY"]
COMPATIBILITY: dict[str, dict] = POLICY["VERSION_COMPATIBILITY"]


def version(value: str) -> tuple[int, ...]:
    """Compare tool versions without a network-backed packaging dependency."""
    return tuple(int(part) for part in re.findall(r"\d+", value))


def pin(name: str) -> str:
    return str(TOKEN_POLICY[name]["default"])


def test_golangci_build_go_is_at_least_the_pinned_go_toolchain():
    """A linter built by an older Go refuses a module targeting the newer Go."""
    required = version(COMPATIBILITY["golangci_lint"]["minimum_go"])
    assert version(pin("GO_VERSION")) >= required


def test_oxlint_and_tsgolint_are_a_supported_pair():
    """oxlint's type-aware loader rejects an older tsgolint package."""
    required = version(COMPATIBILITY["oxlint_tsgolint"]["minimum_tsgolint"])
    assert version(pin("TSGOLINT_VERSION")) >= required


def test_biome_schema_is_sourced_from_the_biome_pin():
    """The schema URL must follow the formatter version, never a second literal."""
    schema = (ASSETS / "lang/ts/biome.json.template").read_text()
    assert '"$schema": "https://biomejs.dev/schemas/@@BIOME_VERSION@@/schema.json"' in schema
    assert re.search(r"schemas/\d+\.\d+\.\d+/schema\.json", schema) is None


def test_rust_toolchain_meets_every_pinned_cargo_tool_msrv():
    """mise installs these binaries with cargo, so rustc must satisfy each MSRV."""
    tool_msrv = COMPATIBILITY["rust_tools"]["minimum_rust"]
    for token, minimum in tool_msrv.items():
        assert version(pin("RUST_VERSION")) >= version(minimum), token


def test_playwright_is_within_axe_peer_range():
    """@axe-core/playwright's peer range starts at playwright-core 1.0.0."""
    minimum = version(COMPATIBILITY["playwright_axe"]["minimum_playwright"])
    assert version(pin("PLAYWRIGHT_VERSION")) >= minimum
    package = (ASSETS / "a11y/ts/playwright/package.json.template").read_text()
    assert '"@axe-core/playwright": "@@AXE_PLAYWRIGHT_VERSION@@"' in package
    assert '"@playwright/test": "@@PLAYWRIGHT_VERSION@@"' in package


def test_node_major_supports_the_pinned_javascript_tools():
    """knip and oxlint declare ^20.19 || >=22.12; Playwright and aws-cdk-lib >=20."""
    assert int(pin("NODE_VERSION")) >= 22


def test_every_mise_pin_uses_a_policy_token():
    """A hard-coded mise value would bypass the one source Renovate updates."""
    assignment = re.compile(r"^\s*[^#][^=]*=\s*\"([^\"]+)\"\s*$")
    for path in sorted(ASSETS.rglob(".mise/conf.d/*")):
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            match = assignment.match(line)
            if not match:
                continue
            value = match.group(1)
            assert value.startswith("@@") and value.endswith("@@"), (
                f"{path}:{line_number} hard-codes mise pin {value!r}"
            )


def test_no_policy_pin_is_repeated_as_a_literal_asset_value():
    """The policy is the only source for tool pins; presets and assets consume tokens."""
    for name, spec in TOKEN_POLICY.items():
        if not spec.get("pin"):
            continue
        value = str(spec["default"])
        literal = re.compile(rf"[\"']v?{re.escape(value)}[\"']")
        offenders = [
            str(path.relative_to(ROOT))
            for path in sorted(ASSETS.rglob("*"))
            if path.is_file() and literal.search(path.read_text())
        ]
        assert offenders == [], f"{name} {value} restated in {offenders}"
