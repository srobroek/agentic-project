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

from project_setup.catalog import ALWAYS_ON

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
