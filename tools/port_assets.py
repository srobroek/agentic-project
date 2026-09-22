#!/usr/bin/env python3
"""Port omp-plugins project-setup asset layers into Copier template layers.

    python3 tools/port_assets.py <assets-root> <templates-dir>

The port is mechanical and re-runnable. Asset bodies are never rewritten except to
turn `# OPTIONAL BEGIN/END` markers into Jinja conditionals:

  *.template          -> *.jinja          (Copier renders only .jinja; everything
                                           else is copied byte-for-byte, which is
                                           what preserves ${{ }} and just's {{ }})
  @@TOKEN@@           -> unchanged        (_envops makes @@ the variable delimiter,
                                           so the existing token syntax IS the
                                           template syntax)
  # OPTIONAL BEGIN x  -> {%- if cond %}   (block/comment delimiters stay Jinja
                                           defaults: `{%` and `{#` appear in zero
                                           files across the corpus)

copier.yml is generated from TOKEN_POLICY, so a token is required, defaulted, or
constrained on purpose -- never silently blank.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------- layers

LAYERS: dict[str, str] = {
    "base": "base",
    "governance": "governance",
    "hooks": "hooks",
    "just": "just",
    "ci": "ci",
    "forge": "forge",
    "release": "release",
    "steering": "steering",
    "worktrunk": "worktrunk",
    "lang-go": "lang/go",
    "lang-python": "lang/python",
    "lang-ts": "lang/ts",
    "lang-rust": "lang/rust",
    "api": "api",
    "i18n": "i18n",
    "a11y": "a11y",
    "infra-aws-cdk": "infrastructure/aws-cdk",
}

# Layers whose asset tree does not map 1:1 onto destination paths. Longest prefix
# wins, so an exact-file rule can override a directory rule.
REMAP: dict[str, list[tuple[str, str]]] = {
    "forge": [
        ("gitlab/.gitlab-ci.yml", ".gitlab-ci.yml"),
        ("github/", ".github/"),
        ("gitlab/", ".gitlab/"),
    ],
    "steering": [
        ("steering-tree/", "docs/agents/"),
    ],
    # The Inlang project lives beside the deployable it translates, so the
    # destination is templated. Empty I18N_DEPLOYABLE puts it at the root.
    "i18n": [
        ("ts/paraglide/project.inlang/", "@@ I18N_PROJECT_DIR @@/"),
        ("ts/paraglide/scripts/", "scripts/"),
        ("ts/paraglide/", ""),
    ],
    # The a11y suite is an isolated package under .a11y/ so it works when the
    # repository has no root package. Three files are also renamed.
    "a11y": [
        ("ts/playwright/playwright.a11y.config.ts.template", ".a11y/playwright.config.ts.template"),
        ("ts/playwright/tests/a11y/a11y.pw.ts.template", ".a11y/tests/a11y.pw.ts.template"),
        ("ts/playwright/package.json.template", ".a11y/package.json.template"),
        ("ts/playwright/.just.d/", ".just.d/"),
        ("ts/playwright/.gitlab/", ".gitlab/"),
        ("ts/playwright/.mise/", ".mise/"),
        ("ts/playwright/.gitignore.d/", ".gitignore.d/"),
    ],
}

# Assets that must not be ported 1:1 because they are instantiated per-item
# rather than once per project.
SKIP_ASSETS: dict[str, set[str]] = {
    "governance": {"ADR.md.template", "LICENSE.source"},
}

# ---------------------------------------------------------------- token policy
#
# required:  no default -> Copier refuses to render if unanswered (verified).
# choices:   enforced on --data input (verified).
# validator: enforced on --data input (verified).

TOKEN_POLICY: dict[str, dict] = {
    "PROJECT_NAME": {
        "type": "str",
        "required": True,
        "help": "Repository name, lowercase with dashes",
        "validator": "^[a-z][a-z0-9-]+$",
    },
    "DESCRIPTION": {"type": "str", "required": True, "help": "One-line purpose"},
    "INSTALL_COMMANDS": {"type": "str", "default": "mise install && just setup"},
    "USAGE_EXAMPLE": {"type": "str", "default": "just dev"},
    "SPDX_ID": {
        "type": "str",
        "choices": ["Apache-2.0", "MIT", "MPL-2.0", "AGPL-3.0-only"],
        "default": "Apache-2.0",
        "help": "Licence. Materialised into LICENSE by a post-copy task",
    },
    "CODEOWNER": {
        "type": "str",
        "required": True,
        "help": "Owner or team for CODEOWNERS, e.g. @me or @org/team",
    },
    "SECURITY_CONTACT": {
        "type": "str",
        "required": True,
        "help": "Private security channel, or the forge advisory URL",
    },
    "CODE_OF_CONDUCT_CONTACT": {
        "type": "str",
        "default": "",
        "help": "Reporting contact. Empty omits CODE_OF_CONDUCT.md",
    },
    "DEFAULT_BRANCH": {
        "type": "str",
        "default": "main",
        "validator": "^[a-z0-9._/-]+$",
    },
    "JOB_TIMEOUT_MINUTES": {"type": "str", "default": "15"},
    "MAX_FILE_KB": {"type": "str", "default": "512"},
    "HOOK_EXCLUDE_PATTERNS": {
        "type": "str",
        "default": "",
        "help": r"Paths excluded from hooks, joined with \|. Empty drops the block",
    },
    "COMMIT_SCOPES": {
        "type": "str",
        "default": "",
        "help": "Allowed commit scopes, comma separated. Empty leaves scopes unrestricted",
    },
    # Versions are pinned, not resolved at run time. Renovate owns the bumps.
    "GO_VERSION": {"type": "str", "default": "1.26"},
    "GOLANGCI_LINT_VERSION": {"type": "str", "default": "2.7.1"},
    "GOVULNCHECK_VERSION": {"type": "str", "default": "1.1.4"},
    "NODE_VERSION": {"type": "str", "default": "24"},
    "BUN_VERSION": {"type": "str", "default": "1.3.2"},
    "BIOME_VERSION": {"type": "str", "default": "2.4.1"},
    "UV_VERSION": {"type": "str", "default": "0.9.8"},
    "RUST_VERSION": {"type": "str", "default": "1.93.0"},
    "CARGO_NEXTEST_VERSION": {"type": "str", "default": "0.9.104"},
    "CARGO_DENY_VERSION": {"type": "str", "default": "0.19.1"},
    "CARGO_MACHETE_VERSION": {"type": "str", "default": "0.9.1"},
    "CARGO_LLVM_COV_VERSION": {"type": "str", "default": "0.6.20"},
    "PYTHON_VERSION": {"type": "str", "default": "3.13"},
    # Derived rather than asked: 3.13 -> 313. Copier renders the default as Jinja.
    "PYTHON_VERSION_NODOT": {
        "type": "str",
        "derive": "@@ PYTHON_VERSION | replace('.', '') @@",
        "help": "Derived from PYTHON_VERSION; override only if you must",
    },
    "FORGE_PLATFORM": {
        "type": "str",
        "choices": ["github", "gitlab"],
        "default": "github",
        "help": "Only github and gitlab are supported; other forges are an explicit gap",
    },
    "FORGE_HOSTNAME": {
        "type": "str",
        "default": "",
        "help": "Self-hosted forge host. Empty means the platform's public host",
    },
    "SETUP_COMMAND": {"type": "str", "default": "just setup"},
    "DEV_COMMAND": {
        "type": "str",
        "default": "",
        "help": "Dev server command. Empty drops the worktree dev-server block",
    },
    "ORG": {
        "type": "str",
        "required": True,
        "help": "Organisation or owner, used for the API contact and CODEOWNERS",
    },
    "REPO_URL": {
        "type": "str",
        "default": "",
        "help": "Remote URL. Empty drops the blocks that reference it",
    },
    "API_TITLE": {"type": "str", "derive": "@@ PROJECT_NAME @@"},
    "API_VERSION": {"type": "str", "default": "0.1.0"},
    "API_DESCRIPTION": {"type": "str", "derive": "@@ DESCRIPTION @@"},
    "API_SERVER_URL": {
        "type": "str",
        "required": True,
        "help": "Production endpoint. An unresolved value is a blocking gap, not a placeholder",
    },
    "API_FAIL_SEVERITY": {
        "type": "str",
        "choices": ["error", "warn", "info"],
        "default": "warn",
        "help": "vacuum lint severity that fails the gate",
    },
    "API_BASELINE_REF": {
        "type": "str",
        "derive": "origin/@@ DEFAULT_BRANCH @@",
        "help": "Git ref the contract diff compares against",
    },
    "BASE_LOCALE": {"type": "str", "default": "en"},
    "LOCALES_JSON": {
        "type": "str",
        "default": '["en"]',
        "help": "JSON array of shipped locales, including the base locale",
    },
    "INLANG_MESSAGE_FORMAT_MODULE_URL": {
        "type": "str",
        "required": True,
        "help": "Exact module URL from the user. There is no safe default to recommend",
    },
    "I18N_PROJECT_DIR": {
        "type": "str",
        "default": "project.inlang",
        "help": (
            "Where the Inlang project lives, relative to the repo root. "
            "For a nested deployable use apps/web/project.inlang"
        ),
        "validator": "^[A-Za-z0-9._/-]+$",
    },
    "I18N_PREPARE_COMMANDS": {
        "type": "str",
        "default": "",
        "help": "Catalog preparation commands, indented four spaces",
    },
    "I18N_CHECK_COMMANDS": {
        "type": "str",
        "default": "",
        "help": "Recurring completeness commands, one per deployable, indented four spaces",
    },
    "AWS_CDK_DEST_SHELL": {
        "type": "str",
        "default": "infrastructure",
        "help": "Repo-relative CDK destination, shell-quoted",
    },
    "A11Y_SURFACES_JSON": {
        "type": "str",
        "default": "[]",
        "help": "JSON array of {name, baseURL, routes}. '[]' records axe scanning as a gap",
    },
    "A11Y_WEB_SERVERS_JSON": {
        "type": "str",
        "default": "[]",
        "help": "JSON array of Playwright {command, url, cwd} objects",
    },
    "A11Y_PREPARE_COMMANDS": {
        "type": "str",
        "default": "",
        "help": "Dependency and fixture setup before Playwright starts, indented four spaces",
    },
    "PLAYWRIGHT_VERSION": {"type": "str", "default": "1.56.0"},
    "AXE_PLAYWRIGHT_VERSION": {"type": "str", "default": "4.11.0"},
    "ADRS": {
        "type": "str",
        "default": "[]",
        "help": (
            "JSON array of ADRs, each {title, decision, rationale, alternatives, "
            "consequences, confirmation}. Composed, not chosen. One file is written per entry"
        ),
    },
    "MONOREPO_MEMBERS": {
        "type": "str",
        "default": "[]",
        "help": (
            "JSON array of monorepo members, each {name, path, capabilities}. "
            "Composed, not chosen. '[]' is the single-root case"
        ),
    },
}

# Booleans a layer needs for its OPTIONAL blocks (not tokens, so declared here).
EXTRA_VARS: dict[str, dict[str, dict]] = {
    "lang-go": {"GO_VENDOR": {"type": "bool", "default": False, "help": "Commit vendor/?"}},
    "lang-rust": {
        "RUST_LIBRARY": {
            "type": "bool",
            "default": True,
            "help": "Library crate? (a binary crate commits Cargo.lock)",
        }
    },
    "lang-python": {
        "PY_SRC_LAYOUT": {
            "type": "bool",
            "default": True,
            "help": "src/ layout? (false for a flat layout)",
        }
    },
    "ci": {
        "IS_MONOREPO": {
            "type": "bool",
            "default": False,
            "help": "Monorepo? Writes .ci/members.json, which drives per-member CI jobs",
        }
    },
}

# OPTIONAL block label -> Jinja condition. An unmapped label is a hard error.
OPTIONAL_MAP: dict[str, str] = {
    "excluded paths": "HOOK_EXCLUDE_PATTERNS",
    "scope allowlist": "COMMIT_SCOPES",
    "no scope allowlist": "not COMMIT_SCOPES",
    "vendor/ not committed": "not GO_VENDOR",
    "library crate": "RUST_LIBRARY",
    "src layout": "PY_SRC_LAYOUT",
    "self-hosted forge": "FORGE_HOSTNAME",
    "dev server": "DEV_COMMAND",
    "REMOTE_EXISTS": "REPO_URL",
}

# Copier post-copy tasks per layer. These are why no wrapper script is needed for
# git init, licence materialisation, or a native language init.
TASKS: dict[str, list[dict]] = {
    "base": [
        {
            "command": ["@@ _copier_python @@", "@@ _copier_conf.src_path @@/tasks/git_init.py"],
            "when": "@@ _copier_operation == 'copy' @@",
        }
    ],
    "governance": [
        {
            "command": [
                "@@ _copier_python @@",
                "@@ _copier_conf.src_path @@/tasks/materialise_license.py",
                "@@ SPDX_ID @@",
            ]
        },
        {
            "command": [
                "@@ _copier_python @@",
                "@@ _copier_conf.src_path @@/tasks/write_adrs.py",
                "@@ _copier_conf.src_path @@/tasks/ADR.md.template",
                "@@ ADRS @@",
            ],
            "when": "@@ ADRS not in ('', '[]', None) @@",
        },
    ],
    "lang-rust": [
        {
            "command": [
                "@@ _copier_python @@",
                "@@ _copier_conf.src_path @@/tasks/native_init.py",
                "rust",
                "@@ 'lib' if RUST_LIBRARY else 'bin' @@",
            ],
            "when": "@@ RUN_NATIVE_INIT @@",
        }
    ],
    "lang-ts": [
        {
            "command": [
                "@@ _copier_python @@",
                "@@ _copier_conf.src_path @@/tasks/native_init.py",
                "ts",
                "@@ PROJECT_NAME @@",
            ],
            "when": "@@ RUN_NATIVE_INIT @@",
        }
    ],
}

# Layers whose tasks are gated on RUN_NATIVE_INIT need that variable declared.
NATIVE_INIT_LAYERS = {"lang-rust", "lang-ts"}

# Tokens a layer needs but that appear in no file body -- referenced only by a
# _task command or a `when:` condition. Declared explicitly so Copier never
# renders against an undefined variable.
EXTRA_TOKENS: dict[str, list[str]] = {
    "governance": ["SPDX_ID", "ADRS"],
    "lang-ts": ["PROJECT_NAME"],
    # Referenced only by a destination path, so the body scanner cannot find it.
    "i18n": ["I18N_PROJECT_DIR"],
}

# Task scripts live in tools/tasks/ and are copied into each layer that needs them,
# so re-porting cannot lose them. They are excluded from the rendered output.
# Assets a task needs to read, copied into <layer>/tasks/ and excluded from output.
# ADR.md.template is instantiated once per manifest entry, which Copier cannot loop.
TASK_ASSETS: dict[str, list[str]] = {
    "governance": ["ADR.md.template"],
}

TASK_SCRIPTS: dict[str, list[str]] = {
    "base": ["git_init.py"],
    "governance": ["materialise_license.py", "write_adrs.py"],
    "lang-rust": ["native_init.py"],
    "lang-ts": ["native_init.py"],
}

ASSETS_ROOT: list[Path] = []

TOKEN_RE = re.compile(r"@@([A-Z0-9_]+)@@")
OPT_BEGIN = re.compile(r"^(\s*)#\s*OPTIONAL BEGIN\s+(.+?)(?:\s+--\s+.*)?$")
OPT_END = re.compile(r"^\s*#\s*OPTIONAL END\s*$")


def convert_optional_blocks(text: str, where: str) -> str:
    out: list[str] = []
    open_labels: list[str] = []
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\n")
        if m := OPT_BEGIN.match(bare):
            indent, label = m.group(1), m.group(2).strip()
            if label not in OPTIONAL_MAP:
                raise SystemExit(f"FATAL {where}: unmapped OPTIONAL label {label!r}")
            open_labels.append(label)
            out.append(f"{indent}{{%- if {OPTIONAL_MAP[label]} %}}\n")
        elif OPT_END.match(bare):
            if not open_labels:
                raise SystemExit(f"FATAL {where}: OPTIONAL END without BEGIN")
            open_labels.pop()
            out.append("{%- endif %}\n")
        else:
            out.append(line)
    if open_labels:
        raise SystemExit(f"FATAL {where}: unclosed OPTIONAL block {open_labels!r}")
    return "".join(out)


def question_block(name: str, spec: dict) -> dict:
    q: dict = {"type": spec.get("type", "str")}
    if "help" in spec:
        q["help"] = spec["help"]
    if "choices" in spec:
        q["choices"] = spec["choices"]
    if spec.get("derive"):
        q["default"] = spec["derive"]
    elif not spec.get("required"):
        q["default"] = spec.get("default", "")
    if "validator" in spec:
        q["validator"] = (
            f"{{% if not ({name} | string | regex_search('{spec['validator']}')) %}}"
            f"{name} must match {spec['validator']}{{% endif %}}"
        )
    return q


FORGE_EXCLUDE = """{% if FORGE_PLATFORM != 'github' %}
/.github/
{% endif %}
{% if FORGE_PLATFORM != 'gitlab' %}
/.gitlab/
/.gitlab-ci.yml
{% endif %}
"""

MONOREPO_EXCLUDE = """{% if not IS_MONOREPO %}
/.ci/members.json
{% endif %}
"""


def write_copier_yml(
    dst: Path, layer: str, tokens: set[str], destinations: set[str] | None = None
) -> dict[str, dict]:
    cfg: dict = {
        "_envops": {
            "variable_start_string": "@@",
            "variable_end_string": "@@",
            "keep_trailing_newline": True,
        },
        "_exclude": ["copier.yml", "tasks", "*.rej", "*.orig"],
    }
    dests = destinations or set()
    touches_forge = any(d.startswith((".github/", ".gitlab/", ".gitlab-ci")) for d in dests)
    if touches_forge:
        cfg["_exclude"].append(FORGE_EXCLUDE)
        tokens = tokens | {"FORGE_PLATFORM"}
    if any(d == ".ci/members.json" for d in dests):
        cfg["_exclude"].append(MONOREPO_EXCLUDE)
    if layer in TASKS:
        cfg["_tasks"] = TASKS[layer]

    questions: dict[str, dict] = {}
    for tok in sorted(tokens | set(EXTRA_TOKENS.get(layer, []))):
        if tok not in TOKEN_POLICY:
            raise SystemExit(
                f"FATAL {layer}: token @@{tok}@@ has no TOKEN_POLICY entry. "
                f"Add one -- a token with no policy would render blank."
            )
        questions[tok] = question_block(tok, TOKEN_POLICY[tok])
    for name, spec in EXTRA_VARS.get(layer, {}).items():
        questions[name] = question_block(name, spec)
    if layer in NATIVE_INIT_LAYERS:
        questions["RUN_NATIVE_INIT"] = {
            "type": "bool",
            "default": False,
            "help": "Run the native toolchain init (cargo/bun) as a post-copy task?",
        }

    body = yaml.safe_dump(cfg, sort_keys=False, width=100)
    if questions:
        body += "\n" + yaml.safe_dump(questions, sort_keys=True, width=100)
    header = "# GENERATED by tools/port_assets.py -- do not hand-edit.\n"
    (dst / "copier.yml").write_text(header + body)
    return questions


def remap(layer: str, rel: str) -> str:
    """Apply the layer's destination rules; longest matching prefix wins."""
    rules = sorted(REMAP.get(layer, []), key=lambda r: len(r[0]), reverse=True)
    for prefix, replacement in rules:
        if rel == prefix or rel.startswith(prefix):
            return replacement + rel[len(prefix) :]
    return rel


def port_layer(src: Path, dst: Path, layer: str) -> tuple[int, int, set[str], set[str]]:
    rendered = verbatim = 0
    tokens: set[str] = set()
    destinations: set[str] = set()
    skip = SKIP_ASSETS.get(layer, set())

    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = f.relative_to(src)
        if rel.name in skip:
            continue
        mapped = Path(remap(layer, rel.as_posix()))
        if rel.name.endswith(".template"):
            destinations.add(mapped.with_name(mapped.name[: -len(".template")]).as_posix())
            target = dst / mapped.with_name(mapped.name[: -len(".template")] + ".jinja")
            target.parent.mkdir(parents=True, exist_ok=True)
            text = f.read_text()
            tokens |= set(TOKEN_RE.findall(text))
            target.write_text(convert_optional_blocks(text, f"{layer}/{rel}"))
            rendered += 1
        else:
            destinations.add(mapped.as_posix())
            target = dst / mapped
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            raw = f.read_bytes().decode("utf-8", "replace")
            if TOKEN_RE.search(raw):
                raise SystemExit(
                    f"FATAL {layer}/{rel}: @@TOKEN@@ in a file without a .template "
                    f"suffix; it would never be substituted."
                )
            verbatim += 1
    return rendered, verbatim, tokens, destinations


def install_tasks(layer: str, dst: Path) -> None:
    """Copy this layer's task scripts into <layer>/tasks/ (excluded from output)."""
    names = TASK_SCRIPTS.get(layer)
    assets = TASK_ASSETS.get(layer, [])
    if not names and not assets:
        return
    source_dir = Path(__file__).parent / "tasks"
    target_dir = dst / "tasks"
    target_dir.mkdir(exist_ok=True)
    for name in names or []:
        src = source_dir / name
        if not src.is_file():
            raise SystemExit(f"FATAL {layer}: task script missing: {src}")
        shutil.copy2(src, target_dir / name)
    for name in assets:
        src = ASSETS_ROOT[0] / LAYERS[layer] / name
        if not src.is_file():
            raise SystemExit(f"FATAL {layer}: task asset missing: {src}")
        shutil.copy2(src, target_dir / name)


# Single-sourced from the package so the port and the CLI cannot disagree.
from project_setup.catalog import ALWAYS_ON, ANSWERS_FILE  # noqa: E402


def want_var(layer: str) -> str:
    return "WANT_" + layer.upper().replace("-", "_")


def build_interview(out: Path, declared: dict[str, dict[str, dict]]) -> int:
    """Generate templates/_interview: the single, bounded question set.

    Derived from the layer configs, so the interview can never drift from what the
    templates actually need. A question owned only by optional layers carries a
    `when:` so it is not asked -- and not recorded -- unless a layer needs it.
    """
    optional = [layer for layer in declared if layer not in ALWAYS_ON]

    owners: dict[str, list[str]] = {}
    specs: dict[str, dict] = {}
    for layer, questions in declared.items():
        for name, spec in questions.items():
            owners.setdefault(name, []).append(layer)
            specs.setdefault(name, spec)

    cfg: dict = {
        "_envops": {
            "variable_start_string": "@@",
            "variable_end_string": "@@",
            "keep_trailing_newline": True,
        },
        "_answers_file": ANSWERS_FILE,
        "_exclude": ["copier.yml"],
        "_message_after_copy": (
            "Answers recorded in " + ANSWERS_FILE + ".\n"
            "Run `project-setup apply --data-file " + ANSWERS_FILE + "` to scaffold."
        ),
    }

    questions: dict[str, dict] = {}
    for layer in optional:
        questions[want_var(layer)] = {
            "type": "bool",
            "default": False,
            "help": f"Include the {layer} layer?",
        }
    for name in sorted(specs):
        spec = dict(specs[name])
        gating = [want_var(layer) for layer in owners[name] if layer not in ALWAYS_ON]
        if gating and len(gating) == len(owners[name]):
            spec["when"] = "@@ " + " or ".join(sorted(gating)) + " @@"
            # A gated question must have a default, or an unselected layer would
            # make Copier demand an answer it will never use.
            spec.setdefault("default", "")
        questions[name] = spec

    dst = out / "_interview"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    body = yaml.safe_dump(cfg, sort_keys=False, width=100, default_flow_style=False)
    if questions:
        body += "\n" + yaml.safe_dump(questions, sort_keys=False, width=100)
    (dst / "copier.yml").write_text(
        "# GENERATED by tools/port_assets.py -- the single question set.\n" + body
    )
    # Copier templates the filename too, and _envops applies there as well.
    (dst / "@@ _copier_conf.answers_file @@.jinja").write_text(
        "# Written by project-setup. Edit by re-running the interview.\n"
        "@@ _copier_answers|to_nice_yaml @@"
    )
    return len(questions)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    assets, out = Path(sys.argv[1]).expanduser(), Path(sys.argv[2])
    if not assets.is_dir():
        raise SystemExit(f"FATAL: assets root not found: {assets}")
    ASSETS_ROOT.clear()
    ASSETS_ROOT.append(assets)

    rows = []
    declared: dict[str, dict[str, dict]] = {}
    for layer, subpath in LAYERS.items():
        src = assets / subpath
        if not src.is_dir():
            raise SystemExit(f"FATAL: layer source missing: {src}")
        dst = out / layer
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        rendered, verbatim, tokens, destinations = port_layer(src, dst, layer)
        install_tasks(layer, dst)
        questions = write_copier_yml(dst, layer, tokens, destinations)
        declared[layer] = questions
        rows.append((layer, rendered, verbatim, len(questions)))

    w = f"{'layer':<12}{'rendered':>9}{'verbatim':>10}{'questions':>11}"
    print(w)
    print("-" * len(w))
    for layer, r, v, q in rows:
        print(f"{layer:<12}{r:>9}{v:>10}{q:>11}")
    print("-" * len(w))
    print(f"{'TOTAL':<12}{sum(r for _, r, _, _ in rows):>9}{sum(v for _, _, v, _ in rows):>10}")
    n = build_interview(out, declared)
    print(
        f"\n_interview: {n} questions "
        f"({len([name for name in declared if name not in ALWAYS_ON])} selection booleans + tokens)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
