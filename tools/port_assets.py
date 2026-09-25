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
from fnmatch import fnmatch
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
    # `licenses` is a task asset, never placed: the task reads the one text it needs
    # from the layer. Placing the pool and deleting it afterwards removed whatever the
    # destination kept under that name, and on a case-insensitive filesystem that
    # included a REUSE-style `LICENSES/` directory.
    "governance": {"ADR.md.template", "LICENSE.source", "licenses"},
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
        # `^[a-z][a-z0-9-]+$` refused the one-letter name `q` and accepted the typo
        # `my-app-`, which is the wrong way round for both. Start with a letter, end
        # with a letter or digit, dashes in between.
        "validator": "^[a-z](?:[a-z0-9-]*[a-z0-9])?$",
        "rule": (
            "be lowercase letters, digits and dashes, starting with a letter and "
            "ending with a letter or digit -- my-app, flint, api2. It becomes the "
            "crate, package and module name"
        ),
    },
    "DESCRIPTION": {"type": "str", "required": True, "help": "One-line purpose"},
    "INSTALL_COMMANDS": {
        "type": "str",
        "default": "mise install && just setup",
        "help": "What a fresh clone runs to install dependencies; shown in the README",
        "tune": True,
    },
    "USAGE_EXAMPLE": {
        "type": "str",
        "default": "just dev",
        "help": "One command the README shows for running the project",
        "tune": True,
    },
    # NONE is for a repository that is not published under a licence: an internal
    # service, a work repo, a private tool. Every other value here is a real licence,
    # so without it the only answers on offer were four ways to publish, and a private
    # repository got an Apache-2.0 LICENSE it never chose -- a statement about the
    # code, not an inconvenience. NONE writes no LICENSE, omits the OpenAPI licence
    # block, and switches cargo-deny to its unpublished-crate policy.
    "SPDX_ID": {
        "type": "str",
        "choices": ["Apache-2.0", "MIT", "MPL-2.0", "AGPL-3.0-only", "NONE"],
        "default": "Apache-2.0",
        "help": "Licence, or NONE for an unpublished repository. Writes LICENSE",
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
        "tune": True,
    },
    "DEFAULT_BRANCH": {
        "type": "str",
        "default": "main",
        "help": "Branch CI listens on and the force-push guard protects",
        # Lowercase-only refused `Development`, which git is perfectly happy with and
        # which some repositories actually use. The pattern exists to keep the value
        # safe inside a workflow and a shell, not to have an opinion about case.
        "validator": "^[A-Za-z0-9._/-]+$",
        "rule": (
            "be a branch name: letters, digits, dot, underscore, slash or dash, "
            "with no spaces -- main, master, release/2.x"
        ),
    },
    "JOB_TIMEOUT_MINUTES": {
        "type": "str",
        "default": "15",
        "help": "Per-job CI timeout; a hung job holds a runner until it fires",
        "tune": True,
    },
    "MAX_FILE_KB": {
        "type": "str",
        "default": "512",
        "help": "Largest file the added-large-files hook accepts",
        "tune": True,
    },
    "HOOK_EXCLUDE_PATTERNS": {
        "type": "str",
        "default": "",
        "help": r"Paths excluded from hooks, joined with \|. Empty drops the block",
        "tune": True,
    },
    "COMMIT_SCOPES": {
        "type": "str",
        "default": "",
        "help": "Allowed commit scopes, comma separated. Empty leaves scopes unrestricted",
        "tune": True,
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
    # One source of truth for the CDK destination. The raw form goes into JSON and
    # into path comparisons; the shell form is derived, because shell-quoting a value
    # and then embedding it in JSON produces invalid JSON the moment a path has a
    # space in it.
    "AWS_CDK_DEST": {
        "type": "str",
        "default": "infrastructure",
        "help": "Repo-relative CDK destination",
        "validator": "^[A-Za-z0-9._][A-Za-z0-9._/-]*$",
    },
    "AWS_CDK_DEST_SHELL": {
        "type": "str",
        "derive": "@@ AWS_CDK_DEST | quote @@",
        "help": "Shell-quoted form of AWS_CDK_DEST; derived, do not set directly",
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
        "compose": True,
    },
    "MONOREPO_MEMBERS": {
        "type": "str",
        "default": "[]",
        "help": "JSON array of {name, path, capabilities}; [] is single-root",
        "compose": True,
    },
}

# Booleans a layer needs for its OPTIONAL blocks (not tokens, so declared here).
EXTRA_VARS: dict[str, dict[str, dict]] = {
    "lang-go": {
        "GO_VENDOR": {
            "type": "bool",
            "default": False,
            "choices": {"Do not commit vendor/": False, "Commit vendor/": True},
            "help": "Vendored dependencies",
        }
    },
    "lang-rust": {
        "RUST_LIBRARY": {
            "type": "bool",
            "default": True,
            "choices": {
                "Library crate": True,
                "Binary crate, which commits Cargo.lock": False,
            },
            "help": "Crate kind",
        }
    },
    "lang-python": {
        "PY_SRC_LAYOUT": {
            "type": "bool",
            "default": True,
            "choices": {"src/ layout": True, "Flat layout": False},
            "help": "Package layout",
        }
    },
    # Derived, not asked. It was the eleventh prompt every project answered, and no
    # interview answer could make it do anything: its whole effect is to place
    # .ci/members.json, whose contents are MONOREPO_MEMBERS -- a composed answer the
    # interview deliberately never asks. So `yes` at that prompt could only produce a
    # manifest naming nobody, which `validate` then reported as ANSWER_HAS_NO_EFFECT.
    # It stays a real answer, so a preset and `--set` still write it and that warning
    # still fires for a caller that sets it without members.
    "ci": {
        "IS_MONOREPO": {
            "type": "bool",
            "derive": "@@ MONOREPO_MEMBERS not in ('', '[]', None) @@",
            "help": "Per-member CI: true when MONOREPO_MEMBERS lists anybody",
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
    "licensed": "SPDX_ID != 'NONE'",
    "unlicensed": "SPDX_ID == 'NONE'",
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
                "@@ _copier_conf.src_path @@/tasks/licenses",
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
                # The crate name, because `cargo init` otherwise names the package
                # after the directory: a scaffold into `ref/` or `my.app/` failed the
                # whole apply ("`ref` cannot be used as a package name"), and any other
                # directory produced a crate that disagreed with PROJECT_NAME.
                "@@ PROJECT_NAME @@:@@ 'lib' if RUST_LIBRARY else 'bin' @@",
                # The licence, because `cargo init` writes no `license` field and
                # cargo-deny falls back to reading the LICENSE file. With SPDX_ID=NONE
                # there is no such file, and `cargo deny check licenses` then fails the
                # crate itself as unlicensed unless the manifest says `publish = false`.
                "@@ SPDX_ID @@",
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
                # The pinned version, because `go mod init` writes whichever Go ran it.
                "@@ GO_VERSION @@",
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
    # AWS_CDK_DEST: the root tsconfig must exclude the CDK project or tsc checks it
    # with the wrong `types` and `just check` fails. Same mechanism as FORGE_EXCLUDE.
    "lang-ts": [
        "PROJECT_NAME",
        "OXLINT_VERSION",
        "TSGOLINT_VERSION",
        "KNIP_VERSION",
        "AWS_CDK_DEST",
    ],
    "lang-rust": ["PROJECT_NAME", "SPDX_ID"],
    "lang-go": ["PROJECT_NAME", "GO_VERSION"],
    "lang-python": ["PROJECT_NAME"],
    # The layer that owns the destination declares it, so the interview asks for the
    # raw path before the shell form derived from it. lang-ts only reads it.
    "infra-aws-cdk": ["AWS_CDK_DEST"],
    # Referenced only by a destination path, so the body scanner cannot find it.
    "i18n": ["I18N_PROJECT_DIR"],
}

# Task scripts live in tools/tasks/ and are copied into each layer that needs them,
# so re-porting cannot lose them. They are excluded from the rendered output.
# Assets a task needs to read, copied into <layer>/tasks/ and excluded from output.
# ADR.md.template is instantiated once per manifest entry, which Copier cannot loop.
TASK_ASSETS: dict[str, list[str]] = {
    "governance": ["ADR.md.template", "licenses"],
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
        # The message a validator prints is the whole of what a user gets, and it is
        # the first refusal anybody meets: `PROJECT_NAME must match ^[a-z][a-z0-9-]+$`
        # left them to read a regex and guess what to type instead. A `rule` says it in
        # words with an example; the pattern stays the enforcer.
        rule = spec.get("rule") or f"match {spec['validator']}"
        q["validator"] = (
            f"{{% if not ({name} | string | regex_search('{spec['validator']}')) %}}"
            f"{name} must {rule}{{% endif %}}"
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

# Never placed, from any layer. Two of these are written *into* `templates/` after the
# port by whatever ran there: importing `templates/<layer>/scripts/x.py` -- which the
# suite does -- leaves `scripts/__pycache__/x.cpython-313.pyc` beside it, and Copier
# then copied that bytecode into every scaffold. A fresh Rust repository carried a
# `.pyc` compiled by whichever interpreter last ran the tests, and `plan` listed it as
# a file to create. The port cannot prevent it, because it happens after the port; the
# exclusion can, because it is evaluated at render time.
JUNK_EXCLUDE: tuple[str, ...] = (
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".ruff_cache",
    ".DS_Store",
)


# Paths a layer places only when they are not already there. Copier overwrites by
# default, and `plan` listed `justfile` twice over: once as a file it would overwrite,
# and once as a path a generator "merged, your entries kept". The second was false --
# the layer replaced the file and the generator then folded its import block into the
# fresh copy, so a brownfield repository's own recipes were gone, and so were any a
# user had added to a scaffold of their own before re-applying. The layer's justfile
# carries no answer (zero @@ tokens), so nothing is re-derived by replacing it and
# nothing is lost by keeping it: the import block is the only generated part, and the
# generator owns that wherever the surrounding file came from.
SKIP_IF_EXISTS: dict[str, list[str]] = {"just": ["justfile"]}


def write_copier_yml(
    dst: Path, layer: str, tokens: set[str], destinations: set[str] | None = None
) -> dict[str, dict]:
    cfg: dict = {
        "_envops": {
            "variable_start_string": "@@",
            "variable_end_string": "@@",
            "keep_trailing_newline": True,
        },
        "_exclude": ["copier.yml", "tasks", "*.rej", "*.orig", *JUNK_EXCLUDE],
    }
    if layer in SKIP_IF_EXISTS:
        cfg["_skip_if_exists"] = SKIP_IF_EXISTS[layer]
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
        # Alphabetical, except that a derived default is emitted after the answers it
        # reads. Copier resolves defaults in declaration order, and IS_MONOREPO sorts
        # ahead of the MONOREPO_MEMBERS it derives from, where the reference would be
        # undefined and `not in ('', '[]', None)` would quietly come out true -- which
        # is `.ci/members.json` placed into every single-root project.
        ordered = sorted(questions, key=lambda name: (is_derived(questions[name]), name))
        body += "\n" + yaml.safe_dump(
            {name: questions[name] for name in ordered}, sort_keys=False, width=100
        )
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


def is_junk(rel: Path) -> bool:
    """A build artifact rather than an asset, wherever it sits in the tree.

    The render-time exclusion above is what keeps these out of a scaffold. This keeps
    them out of `templates/` in the first place, so the generated tree is a function of
    the assets and not of whatever last ran inside them.
    """
    return any(fnmatch(part, pattern) for part in rel.parts for pattern in JUNK_EXCLUDE)


def port_layer(src: Path, dst: Path, layer: str) -> tuple[int, int, set[str], set[str]]:
    rendered = verbatim = 0
    tokens: set[str] = set()
    destinations: set[str] = set()
    skip = SKIP_ASSETS.get(layer, set())

    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = f.relative_to(src)
        if rel.name in skip or rel.parts[0] in skip or is_junk(rel):
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
        if src.is_dir():
            shutil.copytree(src, target_dir / name, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, target_dir / name)
        else:
            raise SystemExit(f"FATAL {layer}: task asset missing: {src}")


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
from project_setup.catalog import (  # noqa: E402
    ALWAYS_ON,
    ANSWERS_FILE,
    LAYER_PURPOSE,
    PIN_GATE,
    SELECTION,
    TUNE_GATE,
)


def want_var(layer: str) -> str:
    return "WANT_" + layer.upper().replace("-", "_")


# The interview asks identity first. A question set that opens with a11y Playwright
# fixtures and asks the repository's name last is technically complete and unusable;
# these two are what the user came to answer.
IDENTITY_FIRST: tuple[str, ...] = ("PROJECT_NAME", "DESCRIPTION")

# The shape, asked straight after identity. FORGE_PLATFORM swaps the entire CI
# surface in one answer, so asking it 27th of 27 -- after every hook threshold and
# job timeout -- put the widest-reaching answer behind the narrowest ones.
SHAPE_NEXT: tuple[str, ...] = ("FORGE_PLATFORM",)

# One multiselect instead of ten `Include the <layer> layer? (y/N)` prompts. Each
# WANT_<LAYER> is then derived from it and never asked, which keeps every preset,
# every layer's `when:`, `--set` and `selected_layers` reading exactly the key they
# already read. SELECTION itself is imported: it is interview-only, like PIN_GATE,
# and the package has to recognise it as a known key rather than a stray answer.
SELECTION_HELP = "Layers to include, beyond the seven every project gets"

# Conditions beyond layer selection. A question whose own answer decides whether it
# is meaningful is gated on that answer, not asked and then ignored.
#
# Empty since IS_MONOREPO became derived: it gated MONOREPO_MEMBERS, which `compose`
# already keeps out of the interview, and the dependency now runs the other way.
ASK_WHEN: dict[str, str] = {}

# One question stands in for every tool-version question. Never asking them at all
# was the wrong end of the trade: the pins are right for almost everybody, and the
# user who needs Python 3.12 or an older Rust had no way to say so short of editing
# the answers file. Asking sixteen versions unprompted was the other wrong end.
# PIN_GATE is imported above, so the port and the CLI cannot disagree about it.
# Every bool the interview ASKS carries `choices`, which makes Copier render a select
# instead of a confirm. A confirm submits on one keypress, so the Enter a user types
# after `y` falls through to the next question and silently accepts its default --
# measured: answering PIN_TOOL_VERSIONS with "y<Enter>" also declined
# CUSTOMISE_DEFAULTS, a question the user never saw. A select consumes its own Enter.
# The value stays a real bool, so every `when:` and every template body is unchanged.
PIN_GATE_SPEC: dict = {
    "type": "bool",
    "default": False,
    "choices": {
        "Keep the pinned versions Renovate bumps": False,
        "Choose the tool versions myself": True,
    },
    "help": "Tool versions",
}

# The same trade, for the thresholds and commands whose defaults are already right:
# a hook size limit, a job timeout, the README's install line. Eight of the 27
# prompts a minimal project answered were these, every one of them correct as
# shipped. Behind one gate they stay reachable without being read out to everybody.
# TUNE_GATE is imported above, for the same reason.
TUNE_GATE_SPEC: dict = {
    "type": "bool",
    "default": False,
    "choices": {
        "Keep the shipped hook, CI and README defaults": False,
        "Change some of them": True,
    },
    "help": "Hook, CI and README defaults",
}


def is_pin(name: str) -> bool:
    """A tool version Renovate owns: asked only behind PIN_GATE."""
    return bool(TOKEN_POLICY.get(name, {}).get("pin"))


def is_tuned(name: str) -> bool:
    """A default that is already right: asked only behind TUNE_GATE."""
    return bool(TOKEN_POLICY.get(name, {}).get("tune"))


def is_composed(name: str) -> bool:
    """An answer assembled from the conversation, never typed at a prompt.

    A JSON array of ADRs is not a question a human can answer in one line, and the
    prompt asking for one was truncated mid-schema anyway. Not asked, still set by
    `--set` and by a data file, and marked `composed` in `catalog --json` so a caller
    can tell it apart from a derived value it must not supply.
    """
    return bool(TOKEN_POLICY.get(name, {}).get("compose"))


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

    Within a layer, an EXTRA_VARS boolean comes before the tokens -- except a derived
    one, which has to come after the answer it reads: IS_MONOREPO is derived from the
    MONOREPO_MEMBERS that TOKEN_POLICY declares.
    """
    layer_order = [n for n in ALWAYS_ON if n in declared] + sorted(
        n for n in declared if n not in ALWAYS_ON
    )
    extra = [(name, spec) for vars_ in EXTRA_VARS.values() for name, spec in vars_.items()]
    declaration = (
        [name for name, spec in extra if not spec.get("derive")]
        + list(TOKEN_POLICY)
        + [name for name, spec in extra if spec.get("derive")]
    )

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
    # One multiselect, not ten yes/no prompts. Each WANT_<LAYER> is derived from it
    # below, so everything downstream still reads the key it always read.
    #
    # Labelled, because the list was ten bare directory names and `worktrunk` or
    # `a11y` tells a first-time reader nothing about what selecting it does. Copier
    # shows the key and records the value, so the answer is still the layer name.
    questions[SELECTION] = {
        "type": "str",
        "multiselect": True,
        "choices": {f"{layer} -- {LAYER_PURPOSE[layer]}": layer for layer in optional},
        "default": [],
        "help": SELECTION_HELP,
    }
    for layer in optional:
        questions[want_var(layer)] = {
            "type": "bool",
            "default": f"@@ '{layer}' in {SELECTION} @@",
            "when": "false",
        }
    # The widest-reaching answer, next. It swaps every layer's CI surface at once.
    for name in SHAPE_NEXT:
        questions[name] = dict(specs[name])
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
    # The same shape for the defaults that are already right. Unconditional: every
    # layer set has at least one of them, and a gate with a `when:` nobody can
    # predict is worse than one question answered no.
    if any(is_tuned(name) for name in specs):
        questions[TUNE_GATE] = dict(TUNE_GATE_SPEC)
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
        if is_composed(name):
            # Assembled from the conversation, not typed at a prompt. Not asked at
            # all, and `--set` and a data file still carry it.
            spec["when"] = "false"
        elif is_pin(name) or is_tuned(name):
            # Behind the one gate, so the default stands unless the user asked to
            # set them. Copier still records it, and --set still overrides it.
            gating.append(PIN_GATE if is_pin(name) else TUNE_GATE)
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
    cfg = yaml.safe_load((out / "_interview" / "copier.yml").read_text())
    asked = [
        key
        for key, spec in cfg.items()
        if not key.startswith("_")
        and isinstance(spec, dict)
        and str(spec.get("when", "")).strip().lower() != "false"
    ]
    plain = [key for key in asked if not cfg[key].get("when")]
    # The number that matters is what a minimal project is actually prompted for; the
    # total counts every gated and layer-specific question nobody sees.
    print(f"\n_interview: {n} declared, {len(asked)} askable, {len(plain)} asked of everybody")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
