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
    "lang-go": "lang/go",
    "lang-ts": "lang/ts",
    "lang-rust": "lang/rust",
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
        }
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
    "governance": ["SPDX_ID"],
    "lang-ts": ["PROJECT_NAME"],
}

# Task scripts live in tools/tasks/ and are copied into each layer that needs them,
# so re-porting cannot lose them. They are excluded from the rendered output.
TASK_SCRIPTS: dict[str, list[str]] = {
    "base": ["git_init.py"],
    "governance": ["materialise_license.py"],
    "lang-rust": ["native_init.py"],
    "lang-ts": ["native_init.py"],
}

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
    if not spec.get("required"):
        q["default"] = spec.get("default", "")
    if "validator" in spec:
        q["validator"] = (
            f"{{% if not ({name} | string | regex_search('{spec['validator']}')) %}}"
            f"{name} must match {spec['validator']}{{% endif %}}"
        )
    return q


def write_copier_yml(dst: Path, layer: str, tokens: set[str]) -> dict[str, dict]:
    cfg: dict = {
        "_envops": {
            "variable_start_string": "@@",
            "variable_end_string": "@@",
            "keep_trailing_newline": True,
        },
        "_exclude": ["copier.yml", "tasks", "*.rej", "*.orig"],
    }
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


def port_layer(src: Path, dst: Path, layer: str) -> tuple[int, int, set[str]]:
    rendered = verbatim = 0
    tokens: set[str] = set()
    skip = SKIP_ASSETS.get(layer, set())

    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = f.relative_to(src)
        if rel.name in skip:
            continue
        if rel.name.endswith(".template"):
            target = dst / rel.with_name(rel.name[: -len(".template")] + ".jinja")
            target.parent.mkdir(parents=True, exist_ok=True)
            text = f.read_text()
            tokens |= set(TOKEN_RE.findall(text))
            target.write_text(convert_optional_blocks(text, f"{layer}/{rel}"))
            rendered += 1
        else:
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            raw = f.read_bytes().decode("utf-8", "replace")
            if TOKEN_RE.search(raw):
                raise SystemExit(
                    f"FATAL {layer}/{rel}: @@TOKEN@@ in a file without a .template "
                    f"suffix; it would never be substituted."
                )
            verbatim += 1
    return rendered, verbatim, tokens


def install_tasks(layer: str, dst: Path) -> None:
    """Copy this layer's task scripts into <layer>/tasks/ (excluded from output)."""
    names = TASK_SCRIPTS.get(layer)
    if not names:
        return
    source_dir = Path(__file__).parent / "tasks"
    target_dir = dst / "tasks"
    target_dir.mkdir(exist_ok=True)
    for name in names:
        src = source_dir / name
        if not src.is_file():
            raise SystemExit(f"FATAL {layer}: task script missing: {src}")
        shutil.copy2(src, target_dir / name)


ALWAYS_ON = ["base", "governance", "hooks", "just"]
ANSWERS_FILE = ".project-setup-answers.yml"


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
        rendered, verbatim, tokens = port_layer(src, dst, layer)
        install_tasks(layer, dst)
        questions = write_copier_yml(dst, layer, tokens)
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
