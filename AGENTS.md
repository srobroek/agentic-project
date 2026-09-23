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

MUST capture fds 1 and 2 **and** `sys.stdout`/`sys.stderr` around `copier.run_copy`. Tasks
are subprocesses that inherit the real descriptors, so without the fd dup a failing task
reports only "returned non-zero exit status 1" and its actual message is lost. Copier's own
per-file announcements go through `print()`, which resolves `sys.stdout` at call time —
under pytest that is a capture object that never touches fd 1, so the fd dup alone loses
every line and the parsed file list comes back empty exactly where it is tested.

MUST keep the interview's declaration order meaningful. `interview_order` asks identity,
then the layer selection, then each layer's own questions. Alphabetical order asked
`PROJECT_NAME` last of 24 and put gated questions ahead of the answers that gate them;
Copier evaluates `when:` in declaration order and a forward reference is silently
undefined, not an error. `_check_declaration_order` fails the port on one.

MUST give every asked question `help`. Without it the prompt is the bare token name, so
the interview asks `MAX_FILE_KB` and tells the user nothing. The port refuses.

MUST mark a tool version with `pin: True` in `TOKEN_POLICY`, which puts it behind the single
`PIN_TOOL_VERSIONS` gate in `_interview` rather than in front of every user or out of reach
of all of them. It reaches `catalog --json` as `"pinned": true`. A value derived from another
answer is not a question at all and gets `when: false`, reported as `"asked": false`. Both
remain settable with `--set`.

MUST add a script to `TASK_SCRIPTS[layer]` when you add a `_task` that runs it. The two
tables are declared apart, so wiring one alone produces a layer that places every file and
then dies with "can't open file" from a path inside `templates/`, which reads like a corrupt
checkout. `check_task_scripts_installed()` fails the port instead.

MUST leave a fresh scaffold able to run its own `just setup` and `just check`. Every tool a
recipe names has to be provided by something the same scaffold installs: a `.mise/conf.d/`
pin, a dependency group, or a package manifest. `tests/test_toolchain.py` enforces it per
layer, because `bunx biome` with biome in no manifest silently resolved a PATH shim.

MUST resolve a generator argument against the catalog defaults, not the raw answers.
Copier applies a layer's default itself; `run_generators` assembles its arguments outside
Copier, so an unanswered `DEFAULT_BRANCH` aborted a half-written scaffold with a KeyError.

MUST let a fragment own what it declares when folding into a shared file. The fragment is
generated from a layer and an answer, so the layer owns that entry: a changed answer has
to land, and a brownfield repository that happens to share one hook id must not fail the
apply. Two *fragments* disagreeing is still a hard error — no answer can resolve it.

MUST report what a run replaced. `plan` parses Copier's own per-file lines and names every
file it would overwrite; that is the only warning before Copier overwrites it.

MUST remove a native tool's leftovers by identity, not by filename. `native_init.py`
records which paths existed before `bun init` ran and removes only what bun created; a
filename check deleted a brownfield repository's own `CLAUDE.md`.

MUST wire a script a layer ships to a task that runs it. `init_aws_cdk.py` shipped with no
caller, so `just aws-cdk-synth` and `just check` failed on a directory nothing created.

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

## Answer policy

MUST keep the hard-required set to `PROJECT_NAME` and `DESCRIPTION`. Anything a user cannot
know at setup time gets a `placeholder` in `TOKEN_POLICY`, which becomes both the default and
Copier's own placeholder marker, and is reported by `validate` and at the end of `apply`.
Blocking a scaffold on a production URL is the wrong trade: the value wanted is visibility.

MUST NOT add a question for something a task can decide. `native_init.py` skips when the
manifest exists and warns when the tool is absent, so `RUN_NATIVE_INIT` was deleted rather
than defaulted.

MUST warn about an answer set that renders cleanly and then does nothing. `IS_MONOREPO`
with an empty `MONOREPO_MEMBERS` wrote a member manifest naming nobody, which `gen_caller`
read as "a monorepo with no members" and used to replace every language job with none at
all. An empty member list is now the single-root case, as the question's own help says,
and `validate` reports `ANSWER_HAS_NO_EFFECT`. A warning, not an error: the combination is
legal and the user may be one answer from meaning it.

MUST reconcile what a native tool writes with what a layer owns. `bun init` drops a generic
CLAUDE.md, its own `.gitignore` and a placeholder `index.ts`. The steering layer owns
CLAUDE.md and refuses to overwrite one it did not write, which failed an otherwise clean
apply; the `.gitignore` would be adopted as unmanaged text above the generated block
forever. `native_init.py` removes all three and sets the package name from `PROJECT_NAME` --
but only for paths that did not exist before `bun init` ran.

MUST exclude `node_modules`, `.git`, `target` and friends from any recursive scan. Native init
populates them and third-party files legitimately contain `@@`.

## Assets are vendored here

`assets/` is the source of truth for file content and `templates/` is generated from it by
`tools/port_assets.py`, which defaults to both. The assets used to live in the omp-plugins
`project-setup` plugin; retiring that plugin removed them, so they were recovered from its
git history into this repository. Do not reintroduce a dependency on another repository.

## Template resolution

MUST NOT bundle `templates/` or `presets/` into the wheel. They are the plugin's payload and
the plugin upgrades independently; a bundled copy would serve stale layers silently. The CLI
resolves them from `--templates`/`--presets`, then `PROJECT_SETUP_TEMPLATES`/
`PROJECT_SETUP_PRESETS`, then a source checkout, and fails naming all three otherwise.

MUST NOT derive the plugin directory from `$0` or the working directory. `$0` in an agent's
shell is the shell itself, and the working directory is the user's target repository. Ask
`omp plugin list --json` for the plugin's own path. `tests/test_resolve.py` covers the chain.

## Verifying a change

    just port          # regenerate templates/ from the vendored assets/
    just check         # ruff, format, and the unit suite
    just e2e           # every preset: validate, plan, apply, re-apply, assertions
    just omp-link      # link and health-check the plugin
    just omp-verify    # read the skill and rule back through OMP

A change that breaks `just e2e` is a regression. The interview has no automated TTY driver:
drive `project-setup interview --dest <tmp>` by hand when you change the question set, and
check what it asks first.
