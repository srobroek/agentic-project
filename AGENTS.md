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
exclusion block. Selecting the forge then swaps the whole CI surface in one answer — for
everything about to be written. An exclusion is not a deletion, so re-applying a repository
with the other answer left the first forge's whole surface in place and still triggering,
while `gen_caller` reported there was no caller to write. `_stale_forge_surface` names those
files as `STALE_FORGE_SURFACE`. A warning, not a deletion: removing a user's CI is not this
tool's call.

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

MUST keep every asked question's `help` inside `MAX_HELP_CHARS`. Copier prints the mic, a
space and the help on one line, and prompt_toolkit truncates that line to the terminal
width with no marker: at 80 columns the version gate read `...which Renova` and the ADRS
prompt stopped at `...alternatives, consequen`. Nine always-asked questions were over.
`_check_help_fits_a_prompt` fails the port. Copier has no second field for the overflow,
so detail that does not fit belongs in `skills/project-setup/SKILL.md`, which is where a
composed answer is assembled anyway. A `placeholder` question does not restate that it is
a placeholder: the prompt pre-fills it, and `validate` and the end of `apply` both name it.

MUST put a question into exactly one of the four not-asked classes, by flag in
`TOKEN_POLICY`, and never by hand in `_interview`. `catalog --json` reports all four, so a
caller checks rather than remembers, and every one of them stays settable with `--set`:

| Flag | `_interview` | `catalog --json` | Why |
| --- | --- | --- | --- |
| `pin: True` | behind `PIN_TOOL_VERSIONS` | `"pinned": true` | a tested version Renovate bumps |
| `tune: True` | behind `CUSTOMISE_DEFAULTS` | `"tuned": true` | a default that is already right |
| `compose: True` | `when: false` | `"composed": true` | a JSON artifact nobody types in one line |
| `derive:` | `when: false` | `"derived": true` | a pure function of another answer |

The two gates exist because both ends of the trade were wrong. Sixteen tool versions and
eight thresholds asked unprompted made a minimal project answer 27 questions, every default
already correct; never asking them put them out of reach of the user who needs Python 3.12.
One question stands in for each set. `composed` and `derived` are both `asked: false` and a
caller must do opposite things with them: a composed value is exactly what it should supply,
a derived one would land a Jinja expression in a file.

MUST select layers through `SELECTION`, the one multiselect, and derive each `WANT_<LAYER>`
from it. Ten `Include the <layer> layer? (y/N)` prompts were ten of those 27 questions. The
booleans remain the canonical answer -- every preset, `--set` and `selected_layers` read
them, and `selected_layers` reads the list too, as a union rather than a fallback. `--dest`
aside, `seed_selection` is the one place that translates: it converts supplied `WANT_*` into
the list's pre-selection and drops the booleans, because supplied data beats a rendered
default and the interview would otherwise ignore every deselection the user made.

MUST label every layer choice with what the layer does, from `LAYER_PURPOSE`. The
multiselect listed ten bare directory names, and `worktrunk`, `a11y` or `infra-aws-cdk`
tells a first-time reader nothing about what selecting it does; `catalog` listed all
seventeen the same way, with a question count. Copier shows a choice's key and records its
value, so the answer is still the layer name. `LAYER_PURPOSE` lives beside `ALWAYS_ON` in
`src/project_setup/catalog.py` for the same reason the gates do: the prompt labels and the
`catalog` listing are two consumers of one line of prose.

MUST keep `PIN_GATE`, `TUNE_GATE`, `SELECTION` and `LAYER_PURPOSE` defined only in
`src/project_setup/catalog.py`, as `ALWAYS_ON` already is. `tools/port_assets.py` imports
them. `PIN_GATE` was declared in both files and they agreed only by luck.

MUST report what an answer no longer selects but the checkout still holds. Copier excludes
what a layer stopped contributing; nothing deletes what an earlier apply wrote, so
re-applying with `WANT_RELEASE: false` left the whole release layer in place. The
destination's own answers file records what was selected last time, so `deselected_layers`
needs no manifest to notice, and `orphaned_files` gets the file list from a pretend place
rather than a static walk -- a layer's paths are templated and conditionally excluded, so
Copier's own list is the only one right by construction. It costs one dry run per dropped
layer, which is why nothing scans until a layer has actually been dropped. A warning, like
`STALE_FORGE_SURFACE`: deleting a user's files is not this tool's call.

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

MUST NOT place a file a generator claims to merge. `plan` listed `justfile` twice: once as
a file it would overwrite, and once as a path a generator "merged, your entries kept". The
second was false — the `just` layer replaced the file and the generator then folded its
import block into the fresh copy, so a brownfield repository's recipes were gone, and so
were any a user had added to their own scaffold before re-applying. `SKIP_IF_EXISTS` in the
port emits `_skip_if_exists` for those paths. It is only safe where the placed file carries
no answer: the layer's justfile has zero `@@` tokens, so keeping an existing one re-derives
nothing. `gen_justfile.py` appends its block to a justfile with no markers rather than
refusing, because that is now the brownfield path.

MUST declare every path a generator rewrites, as the third element of its `GENERATORS` row
and a disposition in `GENERATOR_DISPOSITION`. Copier places none of them —
they are folded from the `.d/` fragments afterwards — so its per-file lines cannot mention
them and `plan` reported "2 existing file(s) would be overwritten" for a brownfield repo
whose `.gitignore`, `.pre-commit-config.yaml` and `AGENTS.md` were all about to be
rewritten as well. `test_every_generator_declares_where_it_writes` refuses a row without one.

MUST report a third disposition, `left-alone`, for a path whose generator refuses to
replace a file it did not write. `gen_caller.py` leaves a `ci.yml` with no generator marker
where it is, so a plan that said "replaced outright" about a brownfield caller threatened a
replacement that never happens — the one line that would make somebody move the file first.
`HAND_OWNED_REFUSAL` names the generator, and the existing file's first line is the test,
because that is where a generated file here names its generator.

MUST make a generator's refusal reachable from where the user is standing. `run_generators`
passes a generator nothing but the destination and its declared arguments, so
`install_agents_index.py` telling a user to "rerun with --claude MERGE" named a flag no
`project-setup apply` could ever pass: the apply exited non-zero over a repository that was
otherwise complete, with its placeholder report suppressed. A destination holding *content*
takes the same non-destructive default AGENTS.md already took — merge, and say what was
folded in. Only a symlink, which is another tool's wiring rather than content, still
refuses, and it names `python3 scripts/install_agents_index.py . --claude SKIP`, which is
installed in the scaffolded repository and therefore runnable.

MUST pass a generator the flag that keeps it from failing a render that must not fail.
`gen_caller.py` documents `--keep-hand-owned` as being "for a render that must not fail",
and `GENERATORS` did not pass it: a brownfield repository with its own CI workflow made the
generator exit 3, which marked the whole apply FAIL and suppressed the placeholder report
over a scaffold that was otherwise complete. Leaving somebody's CI alone is the correct
outcome. The exit code stays 0 and the consequence is reported instead.

MUST surface a step that degraded instead of failing. Tasks run inside Copier, which
captures their output, so `apply` reported `place ok lang-rust`, `95 file(s) created` and
exit 0 for a Rust repository with no `Cargo.toml` and no `src/`: `cargo` was absent, the
task skipped exactly as designed, and the skip went into a log nothing printed. Degrading is
the right trade; degrading invisibly is not. A task or generator marks one by printing
`<name>: WARNING <what did not happen and what to do>`, `TASK_WARNING` collects it, and
`apply` prints the list last, after the placeholder report. `--json` carries it as
`warnings`, because a caller reading only `ok` saw a clean run.

MUST evaluate a declared `validator:` in `validate`, not only at render time. `validate` is
documented as the cheap check that writes nothing and the skill tells an agent to trust it,
so reporting a clean answer set for `PROJECT_NAME=Bad_Name` and then dying on the first
layer makes it useless for the mistake most likely to be made. An expression `validate`
cannot evaluate is reported as `VALIDATOR_NOT_CHECKED`, never silently passed.

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

MUST say a refusal in words, and name what supplies the value. `MISSING_REQUIRED` read
"required by layer(s) base and has no default. One-line purpose", which ran the help onto
the end of a sentence and left the reader to work out that `--set` exists; a `validator:`
printed `PROJECT_NAME must match ^[a-z][a-z0-9-]+$`, a regex with no example of a name that
works. These two are the first refusals anybody meets. A `rule` in `TOKEN_POLICY` carries
the sentence and the pattern stays the enforcer, so the two cannot disagree.

MUST NOT leave Copier's `_`-prefixed bookkeeping in the recorded answers. The interview's
answers file is meant to be committed and carried `_src_path`, an absolute path into the
home directory of whichever machine ran it. Copier writes it for `copier update`, which this
tool does not have, and `load_data` drops every `_` key on the way back in, so it was never
read either. `drop_copier_bookkeeping` removes them.

MUST let a repository state no licence. `SPDX_ID` offered four licences and nothing else, so
an internal service or a work repo got an Apache-2.0 LICENSE it never chose — a statement
about the code, not an inconvenience. `NONE` writes no LICENSE, drops the OpenAPI licence
block, and switches `deny.toml` to `[licenses.private] ignore = true`. That block only
applies to a crate the manifest marks unpublishable, so `native_init.py` writes
`publish = false` for an unlicensed crate: measured with cargo-deny 0.19, without it
`cargo deny check licenses` fails the crate itself as `error[unlicensed]`, because
`cargo init` writes no `license` field and cargo-deny otherwise reads the LICENSE file.

MUST NOT add a question for something a task can decide. `native_init.py` skips when the
manifest exists and warns when the tool is absent, so `RUN_NATIVE_INIT` was deleted rather
than defaulted.

MUST warn about an answer set that renders cleanly and then does nothing. `IS_MONOREPO`
with an empty `MONOREPO_MEMBERS` wrote a member manifest naming nobody, which `gen_caller`
read as "a monorepo with no members" and used to replace every language job with none at
all. An empty member list is the single-root case, and `validate` reports
`ANSWER_HAS_NO_EFFECT`. A warning, not an error: the combination is legal and the user may
be one answer from meaning it. `IS_MONOREPO` is no longer *asked*, because no interview
answer could make it do anything — its whole effect needs `MONOREPO_MEMBERS`, which
`compose` deliberately keeps out of the interview. It is derived from that list, so
listing members is what makes a project a monorepo, and the warning still fires for a
caller that sets the flag alone.

MUST emit a derived question after the answers it reads, in a layer's `copier.yml` as well
as in the interview. Copier resolves defaults in declaration order and the layer dump was
alphabetical, which put `IS_MONOREPO` ahead of the `MONOREPO_MEMBERS` it now derives from:
the reference would be undefined and `not in ('', '[]', None)` would quietly come out true,
placing `.ci/members.json` into every single-root project.

MUST reconcile what a native tool writes with what a layer owns. `bun init` drops a generic
CLAUDE.md, its own `.gitignore` and a placeholder `index.ts`. The steering layer owns
CLAUDE.md and refuses to overwrite one it did not write, which failed an otherwise clean
apply; the `.gitignore` would be adopted as unmanaged text above the generated block
forever. `native_init.py` removes all three and sets the package name from `PROJECT_NAME` --
but only for paths that did not exist before `bun init` ran.

MUST exclude `node_modules`, `.git`, `target` and friends from any recursive scan. Native init
populates them and third-party files legitimately contain `@@`.

MUST exclude `__pycache__`, `*.pyc` and the editor and tool droppings in `JUNK_EXCLUDE` from
every layer. `templates/` is written to *after* the port by whatever runs there: this suite
imports `templates/<layer>/scripts/*.py` by path, which leaves a `__pycache__` beside them,
and Copier copied that bytecode into every scaffold. A fresh Rust repository carried a `.pyc`
built by whichever interpreter last ran the tests, and `plan` listed it as a file to create.
The port cannot prevent it, because it happens after the port; a render-time exclusion can.
A scaffolded repository also ignores `__pycache__/` itself, because two of its own hooks
import a sibling module and the first commit left untracked bytecode in `scripts/`.

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
