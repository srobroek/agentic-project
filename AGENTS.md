# AGENTS.md

Instructions for agents working **on this repository**. For using it to scaffold a project,
read `skills/project-setup/SKILL.md`.

## What this repo is

A deterministic scaffolder. Copier renders the files; this repo owns the layers, the question
set, and the ordering. There is no agent in the hot path: `project-setup apply` produces a
complete repository with no model involved.

## Invariants

MUST keep `templates/` generated. `tools/port_assets.py` writes every `copier.yml` and the
whole `_interview` layer. Hand-editing a generated file is lost on the next port.

MUST declare a token in `TOKEN_POLICY` before using it. The port fails loudly on an
undeclared token rather than emitting a question that renders blank.

MUST keep `@@` as the only changed Jinja delimiter. The corpus audit is the reason:
`{{` appears in 24 asset files and `${{` in 20, while `{%` and `{#` appear in zero. Changing
the block delimiter to `@%` broke a `printf '...@%s'` in a real workflow.

MUST NOT add a `.template` suffix to a file that needs literal `{{ }}`. Copier renders only
`.jinja`; everything else is copied byte-for-byte, which is what preserves GitHub Actions
expressions and justfile interpolation.

MUST run generators after all layers, never as per-layer Copier tasks. A per-layer task fires
before later layers have contributed their fragments.

MUST keep `ALWAYS_ON` in `src/project_setup/catalog.py` as the only definition.
`tools/port_assets.py` imports it; two copies would silently disagree about which layers are
opt-in.

MUST prune empty directories after placing. Copier creates a directory before deciding every
file inside it is excluded, so a GitHub-only project would otherwise ship an empty `.gitlab/`.

MUST declare a destination remap in `REMAP` rather than renaming assets. `forge/github/*` maps
to `.github/*` and `steering/steering-tree/*` to `docs/agents/*`; longest matching prefix wins,
so an exact-file rule can override a directory rule.

MUST gate forge-specific files through `FORGE_EXCLUDE`, not by splitting layers. Any layer that
writes under `.github/` or `.gitlab/` automatically gains the `FORGE_PLATFORM` question and the
exclusion block. Selecting the forge then swaps the whole CI surface in one answer.

MUST derive a value instead of asking for it when it is a pure function of another answer. Use
`derive:` in `TOKEN_POLICY`, which emits a templated default -- `PYTHON_VERSION_NODOT` is
`PYTHON_VERSION` with the dots removed.

MUST pass a generator its required arguments via `GENERATORS` in `runner.py`. `gen_caller.py`
takes `--default-branch`; a caller listening on the wrong branch never runs and reports nothing.

MUST NOT let a templated path segment render empty. Copier drops the file silently, with no
error. Carry the whole sub-path in one token instead -- `I18N_PROJECT_DIR` defaults to
`project.inlang` and expands `apps/web/project.inlang` into nested directories, because Copier
expands `/` inside a rendered segment.

MUST use `TASK_ASSETS` for a template a task needs to read. `governance/ADR.md.template` is
instantiated once per manifest entry, which Copier cannot loop, so `write_adrs.py` owns the loop
and reads the template from the layer's excluded `tasks/` directory.

MUST keep a multi-instance task idempotent by identity, not by filename. `write_adrs.py` matches
on the title slug: the sequence number differs every run, so comparing filenames would write a
duplicate of every ADR each time.

MUST respect a question's declared type when parsing `--set`. A question declared `str` keeps
its raw text. The answers that carry JSON -- `ADRS`, `MONOREPO_MEMBERS`, `LOCALES_JSON`, the
`A11Y_*_JSON` pair -- would otherwise be parsed into Python objects and render as a Python repr
with single quotes, landing in the file as invalid JSON.

MUST capture fds 1 and 2 around `copier.run_copy`, not `sys.stdout`. Tasks are subprocesses
that inherit the real descriptors. Without the fd-level capture a failing task reports only
"returned non-zero exit status 1" and its actual message is lost.

## Adding a layer

1. Add the asset directory to `LAYERS` in `tools/port_assets.py`.
2. Add any new token to `TOKEN_POLICY` with `required`, `default`, `choices`, or `validator`.
   A token with no policy is a hard error, on purpose.
3. Map any `# OPTIONAL BEGIN <label>` to a condition in `OPTIONAL_MAP`. An unmapped label is
   a hard error.
4. List tokens referenced only by a task or a `when:` in `EXTRA_TOKENS`.
5. Re-port, then `project-setup catalog` and `project-setup validate`.

## Adding a task

Tasks live in `tools/tasks/` and are copied into the layers listed in `TASK_SCRIPTS`, then
excluded from the rendered output. A task must be idempotent and must degrade to a warning
when its tool is absent — a scaffold must never hard-fail because `cargo` is missing.

## OMP packaging

OMP recognises an extension package by the `omp` key in `package.json`; the key may be empty.
Capabilities are located by path and cannot be redirected, except `skills` and `commands`
which `.omp-plugin/plugin.json` may remap. A rule with no frontmatter `description` lands in
no bucket, and a frontmatter `name` that disagrees with its filename is not the identity OMP
uses.

MUST keep every capability name prefixed with `project-setup`. OMP deduplicates names across
all configured sources and keeps the first match, so a shared name silently hides one plugin.
`project-setup` is already claimed by the older installed plugin.

## Template resolution

MUST NOT bundle `templates/` or `presets/` into the wheel. They are the plugin's payload and
the plugin upgrades independently; a bundled copy would serve stale layers silently. The CLI
resolves them from `--templates`/`--presets`, then `PROJECT_SETUP_TEMPLATES`/
`PROJECT_SETUP_PRESETS`, then a source checkout, and fails naming all three otherwise.

MUST NOT derive the plugin directory from `$0` or the working directory. `$0` in an agent's
shell is the shell itself, and the working directory is the user's target repository. Ask
`omp plugin list --json` for the plugin's own path. `tests/test_resolve.py` covers the chain.

## Verifying a change

    just port ../omp-plugins/project-setup/skills/project-setup/assets
    just catalog
    just e2e            # every preset: validate, plan, apply, re-apply, assertions
    just omp-link       # link and health-check the plugin
    just omp-verify     # read the skill and rule back through OMP
