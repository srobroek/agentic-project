#!/usr/bin/env python3
"""Port omp-plugins project-setup asset layers into Copier template layers.

    python3 tools/port_assets.py [<assets-root>] [<templates-dir>]

Defaults to the vendored assets/ and templates/ in this repository.

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
    "INSTALL_COMMANDS": {
        "type": "str",
        "default": "mise install && just setup",
        "help": "What a fresh clone runs to install dependencies; shown in the README",
    },
    "USAGE_EXAMPLE": {
        "type": "str",
        "default": "just dev",
        "help": "One command the README shows for running the project",
    },
    "SPDX_ID": {
        "type": "str",
        "choices": ["Apache-2.0", "MIT", "MPL-2.0", "AGPL-3.0-only"],
        "default": "Apache-2.0",
        "help": "Licence. Materialised into LICENSE by a post-copy task",
    },
    "CODEOWNER": {
        "type": "str",
        "placeholder": "@TODO-owner",
        "help": "Owner or team for CODEOWNERS, e.g. @me or @org/team",
    },
    "SECURITY_CONTACT": {
        "type": "str",
        "placeholder": "security@example.com",
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
        "help": "Branch CI listens on and the force-push guard protects",
        "validator": "^[a-z0-9._/-]+$",
    },
    "JOB_TIMEOUT_MINUTES": {
        "type": "str",
        "default": "15",
        "help": "Per-job CI timeout; a hung job holds a runner until it fires",
    },
    "MAX_FILE_KB": {
        "type": "str",
        "default": "512",
        "help": "Largest file the added-large-files hook accepts",
    },
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
    # Tool versions. Pinned, not resolved at run time: Renovate owns the bumps, and a
    # scaffold that reads "latest" builds something different next week. A user who
    # needs a specific one is asked -- once, behind PIN_GATE -- rather than never.
    "GO_VERSION": {
        "type": "str",
        "default": "1.26",
        "pin": True,
        "help": "Go toolchain",
    },
    "GOLANGCI_LINT_VERSION": {
        "type": "str",
        "default": "2.7.1",
        "pin": True,
        "help": "golangci-lint",
    },
    "GOVULNCHECK_VERSION": {
        "type": "str",
        "default": "1.1.4",
        "pin": True,
        "help": "govulncheck",
    },
    "NODE_VERSION": {
        "type": "str",
        "default": "24",
        "pin": True,
        "help": "Node major, for tools that need a Node runtime",
    },
    "BUN_VERSION": {"type": "str", "default": "1.3.2", "pin": True, "help": "Bun"},
    "BIOME_VERSION": {"type": "str", "default": "2.4.1", "pin": True, "help": "Biome"},
    "OXLINT_VERSION": {"type": "str", "default": "1.85.0", "pin": True, "help": "oxlint"},
    "TSGOLINT_VERSION": {
        "type": "str",
        "default": "7.0.2002",
        "pin": True,
        "help": "oxlint-tsgolint, which oxlint's type-aware rules require",
    },
    "KNIP_VERSION": {"type": "str", "default": "6.38.0", "pin": True, "help": "knip"},
    "UV_VERSION": {"type": "str", "default": "0.9.8", "pin": True, "help": "uv"},
    "RUST_VERSION": {
        "type": "str",
        "default": "1.93.0",
        "pin": True,
        "help": "Rust toolchain, written to rust-toolchain.toml",
    },
    "CARGO_NEXTEST_VERSION": {
        "type": "str",
        "default": "0.9.104",
        "pin": True,
        "help": "cargo-nextest",
    },
    "CARGO_DENY_VERSION": {"type": "str", "default": "0.19.1", "pin": True, "help": "cargo-deny"},
    "CARGO_MACHETE_VERSION": {
        "type": "str",
        "default": "0.9.1",
        "pin": True,
        "help": "cargo-machete",
    },
    "CARGO_LLVM_COV_VERSION": {
        "type": "str",
        "default": "0.6.20",
        "pin": True,
        "help": "cargo-llvm-cov",
    },
    "PYTHON_VERSION": {
        "type": "str",
        "default": "3.13",
        "pin": True,
        "help": "Python minor, e.g. 3.13. Sets requires-python and the ruff target",
    },
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
    "SETUP_COMMAND": {
        "type": "str",
        "default": "just setup",
        "help": "What a freshly created worktree runs to become usable",
    },
    "DEV_COMMAND": {
        "type": "str",
        "default": "",
        "help": "Dev server command. Empty drops the worktree dev-server block",
    },
    "ORG": {
        "type": "str",
        "placeholder": "TODO-org",
        "help": "Organisation or owner, used for the API contact",
    },
    "REPO_URL": {
        "type": "str",
        "default": "",
        "help": "Remote URL. Empty drops the blocks that reference it",
    },
    "API_TITLE": {"type": "str", "derive": "@@ PROJECT_NAME @@"},
    "API_VERSION": {
        "type": "str",
        "default": "0.1.0",
        "help": "Version of your contract, not of OpenAPI",
    },
    "API_DESCRIPTION": {"type": "str", "derive": "@@ DESCRIPTION @@"},
    "API_SERVER_URL": {
        "type": "str",
        "placeholder": "https://api.example.com",
        "help": "Production endpoint. Unknown at setup time is normal",
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
    "BASE_LOCALE": {
        "type": "str",
        "default": "en",
        "help": "Locale the source messages are authored in",
    },
    "LOCALES_JSON": {
        "type": "str",
        "default": '["en"]',
        "help": "JSON array of shipped locales, including the base locale",
    },
    "INLANG_MESSAGE_FORMAT_MODULE_URL": {
        "type": "str",
        "placeholder": "https://cdn.jsdelivr.net/npm/@inlang/plugin-message-format@4/dist/index.js",
        "help": "Inlang message-format plugin module URL; pin the version you want",
    },
    "I18N_PROJECT_DIR": {
        "type": "str",
        "default": "project.inlang",
        "help": "Inlang project dir, repo-relative, e.g. apps/web/project.inlang",
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
    # Pinned like every other tool version: `just aws-cdk-init` passes it to the
    # native CDK CLI, which refuses anything but an exact stable release.
    "AWS_CDK_VERSION": {
        "type": "str",
        "default": "2.1142.0",
        "pin": True,
        "help": "aws-cdk CLI, exact stable release",
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
    "PLAYWRIGHT_VERSION": {
        "type": "str",
        "default": "1.56.0",
        "pin": True,
        "help": "Playwright",
    },
    "AXE_PLAYWRIGHT_VERSION": {
        "type": "str",
        "default": "4.11.0",
        "pin": True,
        "help": "@axe-core/playwright",
    },
    "ADRS": {
        "type": "str",
        "default": "[]",
        "help": "JSON array of ADRs, one file written each; [] writes none",
    },
    "MONOREPO_MEMBERS": {
        "type": "str",
        "default": "[]",
        "help": "JSON array of {name, path, capabilities}; [] is single-root",
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
        }
    ],
    "lang-ts": [
        {
            "command": [
                "@@ _copier_python @@",
                "@@ _copier_conf.src_path @@/tasks/native_init.py",
                "ts",
                "@@ PROJECT_NAME @@",
                # `name=version` pairs, not `name@version`: a scoped package name
                # ends in `@` beside the `@@` delimiter, which Jinja then parses as
                # the start of an expression.
                (
                    "@biomejs/biome=@@ BIOME_VERSION @@,"
                    "oxlint=@@ OXLINT_VERSION @@,"
                    "oxlint-tsgolint=@@ TSGOLINT_VERSION @@,"
                    "knip=@@ KNIP_VERSION @@"
                ),
            ],
        }
    ],
    "lang-python": [
        {
            "command": [
                "@@ _copier_python @@",
                "@@ _copier_conf.src_path @@/tasks/native_init.py",
                "py",
                "@@ PROJECT_NAME @@:@@ 'src' if PY_SRC_LAYOUT else 'flat' @@:@@ PYTHON_VERSION @@",
            ],
        }
    ],
    "lang-go": [
        {
            "command": [
                "@@ _copier_python @@",
                "@@ _copier_conf.src_path @@/tasks/native_init.py",
                "go",
                "@@ PROJECT_NAME @@",
            ],
        }
    ],
}

# Tokens a layer needs but that appear in no file body -- referenced only by a
# _task command or a `when:` condition. Declared explicitly so Copier never
# renders against an undefined variable.
EXTRA_TOKENS: dict[str, list[str]] = {
    "governance": ["SPDX_ID", "ADRS"],
    # Referenced only by a _task command, so the body scanner cannot find them.
    "lang-ts": ["PROJECT_NAME", "OXLINT_VERSION", "TSGOLINT_VERSION", "KNIP_VERSION"],
    "lang-go": ["PROJECT_NAME"],
    "lang-python": ["PROJECT_NAME"],
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
    "lang-python": ["native_init.py"],
    "lang-go": ["native_init.py"],
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
    """Build one Copier question from a policy entry.

    Exactly one source supplies the default, in this order:

      required     no default, so Copier refuses to render until it is answered.
                   Reserved for what genuinely cannot be guessed.
      derive       a Jinja expression over other answers, computed at render time.
      placeholder  an obviously-fake value, also recorded as Copier's placeholder so
                   it can be reported afterwards. Setup time is the wrong moment to
                   demand a production URL, so these never block a scaffold.
      default      an ordinary value that is right for most projects.
    """
    q: dict = {"type": spec.get("type", "str")}
    if "help" in spec:
        q["help"] = spec["help"]
    if "choices" in spec:
        q["choices"] = spec["choices"]

    if spec.get("required"):
        pass  # no default: Copier will demand an answer
    elif spec.get("derive"):
        q["default"] = spec["derive"]
    elif "placeholder" in spec:
        # The prompt already shows the placeholder as the pre-filled default, and
        # both `validate` and the end of `apply` name every one still in use. A
        # sentence saying so a third time only pushed the informative half of the
        # help past the terminal width, where prompt_toolkit cut it mid-word.
        q["placeholder"] = spec["placeholder"]
        q["default"] = spec["placeholder"]
    else:
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


TASK_REF = re.compile(r"src_path @@/tasks/([^\s'\"]+)")


def check_task_scripts_installed() -> None:
    """Refuse a `_task` that names a script the port never copies into the layer.

    The two are declared apart -- TASKS names the command, TASK_SCRIPTS names what
    gets installed -- so wiring one without the other produces a layer that places
    its files and then dies with "can't open file", after writing them. Copier
    reports it as a TaskError from a path inside templates/, which reads like a
    corrupted checkout rather than a missing table entry.
    """
    for layer, tasks in TASKS.items():
        installed = set(TASK_SCRIPTS.get(layer, [])) | set(TASK_ASSETS.get(layer, []))
        for task in tasks:
            for part in task["command"]:
                for referenced in TASK_REF.findall(str(part)):
                    if referenced not in installed:
                        raise SystemExit(
                            f"FATAL {layer}: a _task runs tasks/{referenced}, which the port "
                            f"does not install. Add it to TASK_SCRIPTS[{layer!r}] "
                            f"(or TASK_ASSETS for a data file)."
                        )


# Single-sourced from the package so the port and the CLI cannot disagree.
from project_setup.catalog import ALWAYS_ON, ANSWERS_FILE  # noqa: E402


def want_var(layer: str) -> str:
    return "WANT_" + layer.upper().replace("-", "_")


# The interview asks identity first. A question set that opens with a11y Playwright
# fixtures and asks the repository's name last is technically complete and unusable;
# these two are what the user came to answer.
IDENTITY_FIRST: tuple[str, ...] = ("PROJECT_NAME", "DESCRIPTION")

# Conditions beyond layer selection. A question whose own answer decides whether it
# is meaningful is gated on that answer, not asked and then ignored.
ASK_WHEN: dict[str, str] = {
    "MONOREPO_MEMBERS": "IS_MONOREPO",
}

# One question stands in for every tool-version question. Never asking them at all
# was the wrong end of the trade: the pins are right for almost everybody, and the
# user who needs Python 3.12 or an older Rust had no way to say so short of editing
# the answers file. Asking sixteen versions unprompted was the other wrong end.
PIN_GATE = "PIN_TOOL_VERSIONS"
PIN_GATE_SPEC: dict = {
    "type": "bool",
    "default": False,
    "help": "Choose tool versions yourself? No keeps the pinned set Renovate bumps",
}


def is_pin(name: str) -> bool:
    """A tool version Renovate owns: asked only behind PIN_GATE."""
    return bool(TOKEN_POLICY.get(name, {}).get("pin"))


# Every variable an interview question may reference in a `when:` or a derived
# default without being a question itself.
INTERVIEW_BUILTINS: frozenset[str] = frozenset({"_copier_conf", "_copier_operation"})

# Jinja filters and literals that appear inside an expression and are not answers.
_NOT_A_QUESTION = re.compile(r"\|\s*\w+|'[^']*'|\"[^\"]*\"")
_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9_]*\b")


def references(expression: str) -> set[str]:
    """Answer names an interview expression reads."""
    return set(_IDENTIFIER.findall(_NOT_A_QUESTION.sub(" ", expression)))


def interview_order(declared: dict[str, dict[str, dict]]) -> list[str]:
    """The order the interview asks in: identity, selection, then layer by layer.

    Alphabetical order was the bug. It buried PROJECT_NAME behind every A-, B- and
    C-prefixed token and separated a question from the layer it belongs to, and
    Copier evaluates `when:` in declaration order, so it also put gated questions
    ahead of the answers that gate them.

    Within a layer, an EXTRA_VARS boolean comes before the tokens, which is what
    puts IS_MONOREPO ahead of the MONOREPO_MEMBERS it gates.
    """
    layer_order = [n for n in ALWAYS_ON if n in declared] + sorted(
        n for n in declared if n not in ALWAYS_ON
    )
    declaration = [name for extra in EXTRA_VARS.values() for name in extra] + list(TOKEN_POLICY)

    first_owner: dict[str, int] = {}
    for index, layer in enumerate(layer_order):
        for name in declared.get(layer, {}):
            first_owner.setdefault(name, index)

    rank = {name: index for index, name in enumerate(declaration)}
    names = [n for n in first_owner if n not in IDENTITY_FIRST]
    names.sort(key=lambda n: (first_owner[n], rank.get(n, len(rank)), n))
    return [n for n in IDENTITY_FIRST if n in first_owner] + names


def is_derived(spec: dict) -> bool:
    """A default carrying the delimiter is a Jinja expression over other answers."""
    return "@@" in str(spec.get("default", ""))


# Copier prompts with "\U0001f3a4 " and then the help, on one line, and
# prompt_toolkit truncates that line to the terminal width without a marker. On an
# 80-column terminal 77 help characters survive, measured by driving the interview
# through an 80-column PTY: `...which Renova` was the whole of `...which Renovate
# bumps`. One column is left spare so the cursor does not sit in the last cell.
PROMPT_DECORATION = 3
MAX_HELP_CHARS = 80 - PROMPT_DECORATION - 1


def _check_help_fits_a_prompt(questions: dict[str, dict]) -> None:
    """Refuse help that an 80-column terminal would cut mid-word.

    Truncation is silent, so the user does not know a sentence was lost: the ADRS
    prompt ended at `...alternatives, consequen` and the version gate stopped at
    `...which Renova`. Length is the only thing enforceable here, because Copier
    offers no second field to put the overflow in.
    """
    over = {
        name: len(spec["help"])
        for name, spec in questions.items()
        if spec.get("when") != "false" and len(str(spec.get("help", ""))) > MAX_HELP_CHARS
    }
    if over:
        listed = "\n".join(f"    {name} is {width} chars" for name, width in sorted(over.items()))
        raise SystemExit(
            f"FATAL: {len(over)} asked question(s) have help longer than "
            f"{MAX_HELP_CHARS} characters, which an 80-column prompt cuts mid-word:\n"
            f"{listed}\n"
            f"  Shorten the help in TOKEN_POLICY. Detail that does not fit belongs in\n"
            f"  skills/project-setup/SKILL.md, which is where a composed answer is built."
        )


def _check_declaration_order(questions: dict[str, dict]) -> None:
    """Refuse a question that reads an answer Copier has not asked for yet.

    Copier evaluates `when:` and renders a default in declaration order, so a
    forward reference is not an error there -- it silently evaluates to undefined.
    A gate that reads undefined is a question asked when it should not be, or
    skipped when it should not be, with nothing on screen to say so.
    """
    seen: set[str] = set()
    for name, spec in questions.items():
        expressions = [str(spec.get("when", ""))]
        if is_derived(spec):
            expressions.append(str(spec["default"]))
        for expression in expressions:
            for referenced in sorted(references(expression)):
                if referenced in seen or referenced in INTERVIEW_BUILTINS:
                    continue
                if referenced not in questions:
                    continue
                raise SystemExit(
                    f"FATAL {name}: reads {referenced}, which the interview asks later. "
                    f"Copier evaluates in declaration order, so the reference would be "
                    f"undefined. Reorder via EXTRA_VARS/TOKEN_POLICY declaration order."
                )
        seen.add(name)


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
        # Only the fact. The command belongs to the CLI, which is the only side that
        # knows --dest: `apply --data-file .project-setup-answers.yml` pasted from
        # anywhere but the destination applies to the wrong directory or not at all.
        "_message_after_copy": "Answers recorded in " + ANSWERS_FILE + ".",
    }

    questions: dict[str, dict] = {}
    # Identity, then the shape, then the questions the shape implies. A user who
    # typed the command already knows the first two; nothing else is answerable
    # until the layer selection exists, because the selection is what gates it.
    for name in IDENTITY_FIRST:
        questions[name] = dict(specs[name])
    for layer in optional:
        questions[want_var(layer)] = {
            "type": "bool",
            "default": False,
            "help": f"Include the {layer} layer?",
        }
    # Asked once, straight after the selection that decides whether any tool version
    # is in play at all. A scaffold with no language layer pins nothing, so it is not
    # asked there either.
    pin_owners = sorted({layer for name in specs if is_pin(name) for layer in owners[name]})
    if pin_owners:
        gate = dict(PIN_GATE_SPEC)
        optional_owners = sorted(want_var(layer) for layer in pin_owners if layer not in ALWAYS_ON)
        if len(optional_owners) == len(pin_owners):
            gate["when"] = "@@ " + " or ".join(optional_owners) + " @@"
        questions[PIN_GATE] = gate
    for name in interview_order(declared):
        if name in questions:
            continue
        spec = dict(specs[name])
        conditions = [want_var(layer) for layer in owners[name] if layer not in ALWAYS_ON]
        gating = (
            ["(" + " or ".join(sorted(conditions)) + ")"]
            if conditions and len(conditions) == len(owners[name])
            else []
        )
        if name in ASK_WHEN:
            gating.append(ASK_WHEN[name])
        if gating:
            spec["when"] = "@@ " + " and ".join(gating) + " @@"
            # A gated question must have a default, or an unselected layer would
            # make Copier demand an answer it will never use.
            spec.setdefault("default", "")
        if is_pin(name):
            # Behind the one gate, so the default stands unless the user asked to
            # set versions. Copier still records it, and --set still overrides it.
            gating.append(PIN_GATE)
            spec["when"] = "@@ " + " and ".join(gating) + " @@"
            spec.setdefault("default", "")
        elif is_derived(spec):
            # Computed from another answer at render time, so there is nothing to
            # ask. `when: false` keeps the default in force and --set working.
            spec["when"] = "false"
        if spec.get("when") != "false" and not spec.get("help"):
            raise SystemExit(
                f"FATAL {name}: an asked question needs `help`. Without it the prompt is "
                f"the bare token name, which tells a user nothing. Add help to "
                f"TOKEN_POLICY[{name!r}]."
            )
        questions[name] = spec

    _check_declaration_order(questions)
    _check_help_fits_a_prompt(questions)

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
    repo = Path(__file__).resolve().parents[1]
    assets = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else repo / "assets"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else repo / "templates"
    if not assets.is_dir():
        raise SystemExit(f"FATAL: assets root not found: {assets}")
    ASSETS_ROOT.clear()
    ASSETS_ROOT.append(assets)
    check_task_scripts_installed()

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
