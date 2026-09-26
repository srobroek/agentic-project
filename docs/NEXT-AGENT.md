# Brief: shake out `project-setup`

You are picking up a deterministic repository scaffolder. Two shake-out rounds have closed the
obvious defects, so this round is about **improvement**: propose changes and make them. Not
only crashes — friction, needless questions, inflexibility with no reason behind it, and
places where the tool is merely adequate.

Read `README.md` for what it is, `AGENTS.md` for the invariants, and
`skills/project-setup/SKILL.md` for how an agent is meant to drive it.

## This round: two named targets, then your own judgement

The previous round's four are delivered: the journey gate runs in `tools/e2e.py`, the
interview is driven through a PTY, `just check` dispatches `api`, `aws-cdk`, `i18n` and
`a11y` so `fullstack-web` exercises them, and tool versions live only in `TOKEN_POLICY`.
Two things replace them.

### 1. The journey gate is flaky, which is worse than absent

Three consecutive full runs of `tools/e2e.py` gave `12 walked / 0 failed`, then
`11 walked / 1 failed`, then `12 / 0`. It passes on retry. A gate that fails one run in
three for environmental reasons teaches everybody to re-run instead of read it, and it
was installed to be trusted.

The cause is structural rather than a bug: `just setup` and `just check` do real network
work. `bun install`, `cargo fetch`, `uv sync`, prek hook downloads, and `go-vuln` through
a module proxy that does not resolve on this network at all. `go-service` passed three
times in isolation, so the failing preset was not reproducible, which is itself the point.

Find every network-dependent step the journey runs, then decide **per step** whether it
retries, skips with a reported reason, or leaves the gate. A step that skips must say so
in the summary: `0 not walked` is a number the run already prints, and a silent skip is
the failure this whole project keeps finding. Do not make the gate pass by weakening what
it checks.

### 2. Scaffold a monorepo member by running project-setup in it

This is the owner's answer to the member-config design call, and it is better than the
three options put to them: the monorepo shell is scaffolded, then `project-setup` runs
again per member. Nothing is hand-built and nothing guesses at architecture. It also
dissolves the `knip` failure at its root: `knip` only detects plugins from a member's own
configs, and a member scaffolded by the tool has its own.

It does not work yet. Measured today:

```
project-setup apply --preset monorepo --dest R                    # 25 files, a git repo
project-setup apply --preset parts/lang-ts --dest R/services/api  # 69 files
```

The member received the entire root-only surface: a **nested `.git`**, `CODEOWNERS`,
`CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `docs/agents/`, `AGENTS.md`, `CLAUDE.md`,
`.github/`, its own `.pre-commit-config.yaml`. A nested repository inside a member is a
trap on its own. The cause is that the root-only layers are `ALWAYS_ON`, so no selection
can exclude them.

What a member-scoped apply has to do, and the shape is already in the catalog:

- Apply only the member-safe layers. The current `ALWAYS_ON` set is exactly the root-only
  set, so the distinction exists; it just has no mode that honours it.
- Not run `git_init`. It probes `.git` relative to the destination, so in a member it
  creates a nested repository instead of finding the root's.
- Register the member in the root's `.ci/members.json`, which is what drives per-member CI
  and today reports `MEMBER_PATH_EMPTY` for paths nobody created.
- Leave the root's generated files alone. The `.d/` fragments and their generators are
  root-scoped.

Decide the interface and say why. A `--member` flag, a `member` preset tier, and a
separate `add-member` subcommand are all defensible; what matters is that a member cannot
silently receive a nested repository or a second licence. Cover it in the journey gate:
scaffold the monorepo, scaffold both members, then require the root's `just check` and
each member's own checks to pass.

If this turns out to be larger than one round, say so and land the safety half first:
refusing a member-scoped apply that would nest a repository is worth more than the
feature.

## Prove it still works before you change anything

```sh
just port                          # regenerate templates/ from assets/
.venv/bin/python -m pytest -q      # 287 unit tests; addopts has -q, so -o addopts= for a count
.venv/bin/python tools/e2e.py      # 12 stacks end to end, then setup/check/commit in each
slopvac .                          # 7 authored docs fail on style; 8 unfixable are excluded
```

Use `.venv/bin/python -m pytest`, not bare `pytest`: on this machine the interpreter owning
the `pytest` name lacks `copier` and `jinja2`. `tests/conftest.py` catches that and fails
collection with one line naming the right command, rather than five `ModuleNotFoundError`
tracebacks that read like a corrupt checkout. `pythonpath` in `pyproject.toml` puts `src` and
`tools` on the path, so a fresh clone needs no editable install.

`tools/e2e.py` asserts, per stack: validate is clean, `plan` writes nothing, `apply` succeeds,
no `@@` tokens survive, expected files exist, excluded files do not, no build artifacts, no
empty directories, a re-apply changes zero bytes, and the answers file is recorded.

Re-run all four after every change. Breaking `e2e.py` is a regression.

## Count your output; do not `tail` it

Three separate readers of this project have drawn a wrong conclusion from a truncated command:
twice claiming a check passed, once reporting "the four docs" when fifteen files were failing.
When you assert a number, produce it with `wc -l`, `grep -c`, or a summary line. A `tail` is
for reading, never for concluding.

## Leave the owner's style call alone

`slopvac` fails on seven authored documents, dominated by unicode-dash findings — em dashes,
this repo's markdown style. The eight files that cannot be fixed (verbatim SPDX licence texts)
are already excluded in `slopvac.toml`. Whether to restyle the remaining seven or switch
`rules."prose-format.no-unicode-dash"` off is the owner's decision. You may propose; do not
silence it.

## Where things are

| | |
| --- | --- |
| Repo | `/Users/sjors/personal/dev/project-setup` (17 commits, clean, **never pushed**) |
| CLI | `project-setup`, already on PATH, editable install pointing at this repo |
| OMP plugin | `@srobroek/project-setup@0.1.0`, symlinked, so edits are live |
| Slash command | `/project-setup` |
| `assets/` | source of truth for file content |
| `templates/` | **generated** from `assets/` by `tools/port_assets.py`. Never hand-edit |
| `presets/` | 12 stacks, plus `presets/parts/` with 12 single-concern parts |

## What had never been exercised, and what it found

All four have now been driven for real. What each one produced is recorded here so the next
reader does not repeat it.

1. **The interactive interview.** Driven through a PTY at 80, 120 and 200 columns. It asked
   27 prompts for the most minimal project; ten were one-layer-at-a-time yes/no questions and
   eight were defaults already correct, so it now asks thirteen. Deselecting a language does
   stop its questions. Nine prompts were truncated mid-word by the terminal width; fixed and
   now enforced by `_check_help_fits_a_prompt`. Its closing line named an `apply` command with
   no `--dest`; the CLI prints the whole pipeline instead. There is still no TTY driver in the
   suite — the properties are pinned on the generated question set, which is where they live.
2. **The agent interview.** Run headless against a greenfield directory. It classified the
   repository, offered two composed shapes and a manual path in one round, and recommended
   one. No handover-and-stop, and nothing asked that a task or a derived value decides.
3. **Brownfield.** Applied into a repo with its own `.pre-commit-config.yaml`, `.gitignore`,
   `justfile`, `AGENTS.md` and `CLAUDE.md`, on `master`. The merges hold: every hand-written
   hook and ignore line survived. Two defects: the apply failed outright on the existing
   `CLAUDE.md` naming a flag no caller can pass, and `plan` never mentioned the four files the
   generators were about to rewrite. Both fixed. A ninth round went further -- `tsconfig.json`,
   `biome.json`, `package.json` with real dependencies, an MIT `LICENSE` against an
   `SPDX_ID=Apache-2.0` answer, and a dirty working tree -- see "What the ninth round fixed".
4. **`init_aws_cdk.py`** runs. `just aws-cdk-init` generated the app, `bun install` resolved
   294 packages, and `just aws-cdk` synthesized CloudFormation. It removes the `.npmignore`
   `cdk init` leaves; `cdk`'s own `infrastructure/.gitignore` stays, which is correct for its
   own subtree. Re-running refuses rather than clobbering, as its recipe comment says.

## Hunt specifically for

- **Questions that should not be asked.** Anything derivable from another answer, decidable
  by a task, or already fixed by the chosen preset.
- **Refusals that should be warnings.** Setting a project up is not the moment to demand a
  production URL.
- **Inflexibility with no reason.** If a user wants a combination the presets do not name,
  can they get it? If an answer needs changing later, can they change it?
- **Output a human has to decode.** Error messages that name no fix. Summaries that bury the
  one line that matters. `None` where a value belongs.
- **Anything a native tool leaves behind** that collides with a layer, or looks like junk in
  a fresh repo.

## Design decisions to respect, each learned by getting it wrong

These are not preferences. Each replaced a mistake found in use, so re-introducing one is a
regression even if it looks tidier.

1. **Always interview.** A preset is where the conversation starts, not a substitute for it.
   An earlier version told the agent to hand over one command and stop; that was
   unexecutable, because you cannot know whether a preset fits until you have asked.
2. **Never block on a value nobody knows yet.** Only `PROJECT_NAME` and `DESCRIPTION` are
   required. Everything else defaults or carries a visible placeholder, reported by
   `validate` and listed at the end of `apply`. The goal is visibility, not a wall.
3. **Never ask what a task decides.** `native_init.py` skips when its manifest exists and
   warns when its tool is absent, so there was no decision for a user to make.
4. **Never ask for a `derived` value, and know which of the four classes a question is in.**
   `catalog --json` marks all four. `derived` holds an expression in `derived_from`, which is
   not a value to pass through; `composed` is the JSON artifact a caller is expected to
   assemble and supply; `pinned` and `tuned` are real questions, each behind one gate.
5. **Only `.jinja` files render.** `@@` is the variable delimiter, chosen by auditing the
   corpus: `{{` appears in 24 asset files and `${{` in 20, while `{%` and `{#` appear in
   none. That is what keeps GitHub Actions and justfile syntax intact.
6. **Generators run once, after every layer.** As a per-layer Copier task they would fire
   before later layers had contributed their fragments.
7. **Templates are not bundled into the wheel.** The plugin upgrades independently and a
   bundled copy would serve stale layers silently. Resolution is `--templates`, then
   `PROJECT_SETUP_TEMPLATES`, then a source checkout. After those comes the plugin that
   `omp plugin list --json` reports at run time, and an error naming all four.
8. **Exclude `node_modules`, `.git`, `target` from recursive scans.** Native init populates
   them and third-party files legitimately contain `@@`.
9. **Never derive the plugin path from `$0` or the working directory.** `$0` is the shell and
   the working directory is the user's target repo. Ask `omp plugin list --json`.

## Resolved: the merged-answer constraint

This section used to say that changing an answer already folded into a shared file was
refused, and that making it re-derive would be a real improvement. It re-derives. A new
`COMMIT_SCOPES` replaces the `conventional-pre-commit` entry in `.pre-commit-config.yaml`
and `merge_hooks.py` reports which entry it replaced; a hook no fragment declares is left
alone, so a brownfield repository's own hooks survive. `merge` in `assets/hooks/scripts/`
carries the reasoning, and
`tests/test_scaffold.py::test_changing_a_merged_answer_re_derives_the_generated_file` pins it.

## Resolved: five judgement calls the owner took

These were recorded here as deliberately-left-alone, and then decided the other way. Each
is now implemented, and the reasoning lives next to the code that carries it.

1. **The layer selection is one multiselect.** Ten `Include the <layer> layer? (y/N)` prompts
   became `LAYERS`, and each `WANT_<LAYER>` is derived from it. The booleans stay the
   canonical answer, so no preset changed; `selected_layers` reads both spellings as a union,
   and `seed_selection` is the only translation point.
2. **The shipped defaults sit behind one gate.** `CUSTOMISE_DEFAULTS`, the same trade
   `PIN_TOOL_VERSIONS` already made, for `MAX_FILE_KB`, `JOB_TIMEOUT_MINUTES`,
   `HOOK_EXCLUDE_PATTERNS`, `COMMIT_SCOPES`, `CODE_OF_CONDUCT_CONTACT`, `INSTALL_COMMANDS`
   and `USAGE_EXAMPLE`. `catalog --json` marks them `"tuned": true`.
3. **`ADRS` and `MONOREPO_MEMBERS` are not prompts.** `compose: True` in `TOKEN_POLICY`,
   `when: false` in the interview, `"composed": true` in `catalog --json` — a fourth class,
   distinct from `derived` because a caller must supply one and must never supply the other.
4. **A deselected layer's leftovers are named.** `deselected_layers` reads the destination's
   own recorded answers, and `orphaned_files` gets the file list from a pretend place rather
   than the per-layer manifest this entry assumed was needed. Reported as
   `STALE_LAYER_FILES`.
5. **`FORGE_PLATFORM` is asked third**, straight after the layer selection, via `SHAPE_NEXT`.

## Found, judged, and deliberately left alone

Neither of these was fixed. Each is recorded with its file and its symptom so the finding
survives. This repository has no ledger, so this list is the carrier.

1. **`catalog --json` no longer carries the `ADRS` schema.** It moved out of `help` to fit
   an 80-column prompt, and now lives only in `skills/project-setup/SKILL.md`. A non-agent
   reader of `catalog --json` has one more hop. The alternative is a `detail` field the port
   keeps out of the interview: more machinery, and a second string per token to drift.
   Check: the schema is reachable from `catalog --json` without lengthening any prompt.
2. **`README.md`, `AGENTS.md`, `SKILL.md`, this file — `uvx slopvac` fails on all four, and
   did so before any of this work.** Measured twice: 71.4, then 71.0 before the second
   shake-out and 69.9 after it, the same failure classes both times — Unicode dashes and the
   `prose-format` budget. The em dash is this repository's own markdown style, so the fix is a
   deliberate style change across every document, not a patch to whichever paragraph was
   edited last. Check: `uvx slopvac` passes on all four, and the Python comments still read
   the way they do now.

3. **The owner's style call on `slopvac`** is the only one of these still open. Items 3 to 5
   below were recorded here by the fifth round and have since been fixed; they are kept as
   entries because each one's reasoning is worth more than its diff.

## What the sixth round fixed

1. **`@types/bun` was `"latest"` in every TypeScript scaffold.** `bun init` writes it, and the
   task that adds the other dev tools only filled in missing keys, so the one floating version
   in a manifest whose every other dependency is exact survived. `bun.lock` pinned it after
   setup, but a fresh resolve elsewhere could take a different version of the types that define
   the runtime. `TYPES_BUN_VERSION` now carries it. The task replaces `"latest"` and nothing
   else: a brownfield repository's `^2.0.0` is somebody's decision. A `TOKEN_POLICY` entry alone
   was not enough: a token referenced only by a task command must also be declared in
   `EXTRA_TOKENS`, or it renders empty, which is how the first attempt wrote `"@types/bun": ""`.
2. **`just setup` installed the developer's whole global mise toolset.** A minimal scaffold
   declares 13 tools and installed 116 on a machine with a populated `~/.config/mise`, because
   `mise install` with no scope reads every config in scope. No mise flag restricts it, and
   `MISE_CONFIG_FILE=/dev/null` suppresses everything including the project's own. Pointing
   `MISE_GLOBAL_CONFIG_FILE` at an empty real file for the install alone gives exactly the
   project's 13 and leaves the rest of the environment untouched, so later steps still see
   whatever the developer configured. `/dev/null` fails there too: mise cannot infer its type.
3. **`slopvac` was unpinned in this repository**, so the gate's verdict depended on which
   interpreter mise resolved in a given directory. Pinned as `pipx:slopvac`, which mise has
   even though a bare `slopvac` is not in its registry.
4. **`just go-vuln` failed with a bare timeout naming no fix.** `proxy.golang.org` does not
   resolve on this network and `go run` downloads through it. The recipe now names
   `GOPROXY=direct`, gated on the proxy really not answering so a genuine vulnerability report
   never carries a network excuse, and on exit 127 so a missing `curl` is not read as a dead
   proxy. Verified on four paths: dead proxy, reachable-but-failing, `GOPROXY=off`, success.
   The exit-code shortcut was considered and rejected: `govulncheck` exits 1 here whether the
   module is vulnerable or the database is unreachable, so the code cannot carry the diagnosis.

## What a second shake-out found, and fixed

Driven again: the interview through a PTY, `/project-setup` headless against a greenfield
directory and a brownfield Python repo on `master`, and `apply` with a PATH that has no
`cargo`. Eight defects, each with a test named after its symptom.

1. **A skipped task reported success.** With `cargo` absent, `apply` printed
   `place ok lang-rust`, `95 file(s) created` and exit 0 for a Rust repository with no
   `Cargo.toml` and no `src/`. Tasks run inside Copier, which captures their output, so the
   skip went into a log nothing printed. Tasks now mark a degradation `WARNING`, `apply`
   prints them last, and `--json` carries `warnings`.
2. **Every scaffold carried a `.pyc`.** This suite imports `templates/<layer>/scripts/*.py`
   by path, which leaves a `__pycache__` beside them; Copier then copied that bytecode into
   the output, and `plan` listed it as a file to create. The headless agent noticed before
   any of the checks did. `JUNK_EXCLUDE` excludes it at render time; `e2e.py` asserts it.
3. **A fresh repository grew untracked bytecode.** Two hook scripts import a sibling module,
   so the first commit wrote `scripts/__pycache__/` into a repository whose `.gitignore` did
   not mention it unless the Python layer happened to be selected. The base layer owns the
   fragment now.
4. **`plan` lied about the justfile.** It listed the file twice, once as an overwrite and
   once as "merged, your entries kept". The layer replaced it and the generator folded its
   block into the fresh copy, so a brownfield repository's recipes were gone. The `just`
   layer now declares `_skip_if_exists`, and `gen_justfile.py` appends its block to a
   justfile with no markers.
5. **A hand-owned CI workflow failed the apply.** `gen_caller.py` correctly refuses a
   `ci.yml` it did not write, and `GENERATORS` did not pass the `--keep-hand-owned` flag the
   script documents for exactly this caller: exit 3, `FAIL`, placeholder report suppressed,
   over a scaffold that was otherwise complete. `plan` also promised to replace the file.
   Three dispositions now, `left-alone` among them.
6. **Two refusals were unreadable.** `MISSING_REQUIRED` ran the help onto the end of a
   sentence and named no way to supply the value; a validator printed
   `PROJECT_NAME must match ^[a-z][a-z0-9-]+$`. A `rule` in `TOKEN_POLICY` says it in words.
   The pattern was wrong at both ends too: it refused the one-letter name `q` and accepted
   the typo `my-app-`.
7. **`IS_MONOREPO` was asked of everybody and could do nothing.** Its whole effect needs
   `MONOREPO_MEMBERS`, which `compose` keeps out of the interview, so the only reachable
   answer was the one `validate` then reported as `ANSWER_HAS_NO_EFFECT`. Derived from the
   member list now; ten prompts, not eleven. A layer's `copier.yml` emits a derived question
   after the answers it reads, because the dump was alphabetical.
8. **The layer list was ten bare directory names** and `catalog` listed seventeen the same
   way. `LAYER_PURPOSE` labels both.

Two smaller ones: the interview recorded `_src_path`, an absolute path into the home
directory of whichever machine ran it, into a file meant to be committed; and
`gen_steering.py` said "wrote 8 of 9 steering file(s)", which reads as one file having failed
when the ninth was already correct.

Two judgement calls taken the other way:

- **`SPDX_ID` takes `NONE`.** Four choices were four ways to publish, so an internal service
  got an Apache-2.0 LICENSE it never chose. Measured while implementing it: `[licenses.private]
  ignore = true` alone does not satisfy cargo-deny, because it only applies to a crate the
  manifest marks unpublishable.
- **`WANT_CI: false` gets its own code.** `ALWAYS_ON_LAYER` rather than `UNKNOWN_KEY`, which
  sent somebody looking for a spelling mistake in the name of a layer that exists.

Left alone deliberately: the always-on set is still not deselectable, and an agent asked to
"decide everything and apply without waiting" still stops for plan approval. The second is
the gate working; removing it would let a model overwrite files on its own judgement.

## What the third round found, and fixed

Each has a test named after its symptom, listed in `AGENTS.md` beside the invariant it
protects.

1. **`plan` predicted instead of measuring.** Its report was assembled from Copier's
   per-file lines and two static tables, with tasks off. Measured on minimal and rust-cli,
   the old plan disagreed with apply on both greenfields: four
   `licenses/*.txt` it promised and no apply left, and the LICENSE, `.gitignore`, `ci.yml`
   and AGENTS.md it never mentioned. On a no-op re-apply it named seven `docs/agents` files
   as overwritten. `plan` now rehearses the real apply in a copy and reports the
   difference; `tools/plan_property.py` found 0 mismatches in 48 runs.
2. **An interrupted apply left no trace.** Re-running always recovered, byte for byte, at
   all 60 layer and generator boundaries measured. Nothing said the run was incomplete, so
   `.project-setup-incomplete` does now. A kill inside a native init did not recover: the
   manifest was skipped as present forever. `native_init.py` marks an init pending.
3. **Every command outside a checkout needed two flags.** The CLI asks OMP itself.
4. **The interview skipped every question a preset answered**, the layer selection
   included, so `--preset rust-cli` could not drop Rust. Ctrl-C and Ctrl-D printed two
   tracebacks, and a second interview started from blank.
5. **Apply deleted things it had not been asked to.** Every empty directory in the
   destination, including inside `node_modules`, and a REUSE `LICENSES/` directory on a
   case-insensitive filesystem.
6. **A crate was named after its directory.** Scaffolding into `ref/` failed the apply.
7. **`just check` failed after `just aws-cdk-init` on fullstack-web.** gofmt, golangci-lint
   and `go test` walked into the CDK app's `node_modules`, and oxlint rejected two bare
   `.sort()` calls in the i18n drift script. Also fixed: `go.mod` said the Go that ran
   `go mod init`, not the pinned one.

Resolved in the fourth round: root `tsc --noEmit` type-checked the CDK app's jest test, so `just check` on
fullstack-web fails after `just aws-cdk-init` with TS2593. The fix needs the lang-ts
tsconfig to exclude the CDK destination, which means one layer reading whether another is
selected; nothing does that yet, and it is a design call.

## What the fourth round fixed

All found by running apply, just setup, just check by hand. Every one of them passed
`pytest` and `tools/e2e.py` throughout, which is why target 1 exists.

- **`just check` rewrote the tree.** Every language aggregate depended on a writing
  formatter, and the description said so. Each language gained a `<lang>-fmt-check`; the
  writer stays as `<lang>-fmt` and `just each fmt`.
- **`just setup` failed whenever the CDK layer was selected.** `aws-cdk-install` did a bare
  `cd` into a destination `aws-cdk-init` had not created. Both it and `aws-cdk-synth` now
  stand down, install quietly because setup runs it unasked.
- **A fresh Go scaffold failed its own lint.** golangci-lint pinned at 2.7.1, built against
  go1.25, against a toolchain pinned at 1.26. Bumping the policy did nothing because the
  preset restated the version and won.
- **The a11y layer had never worked.** Its Playwright config used `import.meta.url`, which
  the CJS transform that loads it cannot take, so it died before any test ran.
- **The a11y answers could not be formatted.** A JSON answer has quoted keys, which no JS
  formatter emits, and its length decides the layout — so no fixed layout in a `.ts` file
  could satisfy every answer. The data moved to JSON files read at run time.
- **The web-ui part shipped an example web server that did not exist**, so a fresh scaffold
  failed trying to start it. It ships empty now, which the help already called a recorded gap.
- **A keypress answered the next question.** All five asked bools are selects now.
- **A rejection message was cut off mid-sentence** at 80 columns. Context moved to `help`.
- **The formatter owned the CDK app** the type-checker had already let go, so check passed on a
  fresh scaffold and failed the moment `aws-cdk-init` ran.

## What the fifth round found, and fixed

The journey is now a gate: `tools/e2e.py` walks setup, check, first commit and a clean tree in
every preset with the pinned toolchain, and again after `just aws-cdk-init`. Measured before
any fix, by hand over the same sequence: `just check` passed on 11 of 12 unstaged scaffolds,
and after `git add -A` it failed on 3. After the fixes: 12 walked, 0 skipped, 0 failed.

1. **`just check` on an unstaged scaffold checked nothing.** `prek run --all-files` reads
   tracked files, so every hook said "(no files to check)". The journey stages first.
2. **The i18n gate was empty.** `just i18n` ran nothing; the drift script had no caller and
   no catalog to read; the fragment's trailing blank line failed the first commit on
   web-app and fullstack-web. Fixed in the layer.
3. **The monorepo preset failed its own `just check`** on biome formatting `.ci/members.json`,
   a composed JSON answer no fixed layout satisfies. biome.json now skips it, as it skips the
   a11y JSON.
4. **The monorepo member CI path points at directories that do not exist.** Apply writes the
   starters at the root; each member job runs only when its own path changes. `validate`,
   `plan` and `apply` now report `MEMBER_PATH_EMPTY`. Not fixed: with the code moved into
   `services/api` and `apps/web` by hand, every Go job step and the TypeScript biome, oxlint
   and test steps pass, and `tsc` passes once the member has a one-line tsconfig extending the
   root. `knip` still fails: it reports biome, oxlint and tsgolint as unused and
   `index.test.ts` as an unused file, because it only detects plugins from the member's own
   configs. Writing per-member configs means one layer placing files at a path another
   answer names; it is a design call.
5. **Version bumps broke scaffolds that every unit test passed.** zizmor 1.30.1's
   `self-repository` audit failed eleven presets, so zizmor is held at 1.28.0. Python 3.14
   made ruff rewrite `scripts/` into `except A, B:`, which 3.13 cannot parse; `scripts/**`
   keeps `py312`. A govulncheck binary mise cached under go1.26 refused the go1.27 module;
   `just go-vuln` builds it with `go run` at the pin.
6. **Four oxlint warnings in shipped files**, from the a11y test's default import and the
   drift script. Gone.

Covered by: `tests/test_e2e_journey.py`, `tests/test_interview_pty.py`, `tests/test_versions.py`,
the three i18n tests and the per-preset end-of-file test in `tests/test_scaffold.py`, and the
two `MEMBER_PATH_EMPTY` tests in `tests/test_catalog.py`. `just go-vuln` has no permanent
test: it needs the vulnerability database. On this machine `proxy.golang.org` does not
resolve, so it was run with `GOPROXY=direct`.

## What the ninth round fixed (brownfield)

A fixture repo, not a toy: a real MIT `LICENSE`, its own `.pre-commit-config.yaml` with a
pinned `black` rev, a `justfile` with `setup`/`test`/`check` recipes, `tsconfig.json`,
`biome.json`, a `package.json` with real dependencies and its own lint/typecheck/test
scripts, a `.github/workflows/ci.yml`, a hand-written `.gitignore` and `README.md`, a
committed `src/index.ts`, and a dirty working tree. Applied `fullstack-web` on top with
`project-setup apply --preset fullstack-web --dest .`. Four defects, all silent -- `apply`
exited 0 and reported a clean run every time -- and all four now print a
`<script>: WARNING ...` line that `apply`'s top-level `warnings` carries.

1. **A brownfield `package.json` or `pyproject.toml` never got the tools its own recipes
   call.** `native_init.py`'s `ts`/`py` branches return before running `bun`/`uv` at all when
   the manifest already exists, which is correct -- but the early return also skipped
   `_declare_ts_dev_tools`/`_declare_dev_tools`, the one step that is safe and desired
   regardless of freshness. Measured: `just check`'s `bunx biome`/`bunx oxlint` would have
   fallen through to an unversioned mise shim or PATH, exactly the failure mode the
   functions' own docstrings describe, and nothing said so. Both branches now call the
   declare step on the early-return path too; `_declare_ts_dev_tools` and
   `_declare_dev_tools` already merge by missing key, so an existing version is never
   overwritten. `tests/test_native_tools.py::test_a_brownfield_package_json_still_gets_the_dev_tools`
   and `::test_a_brownfield_pyproject_still_gets_the_dev_tools`, each asserting `run` is
   never called.
2. **An SPDX_ID answer that disagreed with an existing LICENSE was accepted with no warning
   at all.** `materialise_license.py` prints "LICENSE already present, leaving it alone" with
   no `WARNING` marker, so `SPDX_ID=Apache-2.0` against a committed MIT `LICENSE` scaffolds
   clean and the repository keeps stating MIT, with nothing in `plan`, `apply`, or `validate`
   naming the disagreement -- `ANSWER_CONTRADICTS_REPO` in `catalog.py` is the forge/CI check
   only, not this. Fixed by adding the marker and naming the fix (delete `LICENSE` and
   re-run). `tests/test_scaffold.py::test_a_brownfield_licence_mismatch_is_a_warning_not_silence`.
3. **A hook a fragment declares under `repo: builtin` and a hook the repository already ran
   under the classic `pre-commit/pre-commit-hooks` repo are a different `(url, id)` key, so
   `merge_hooks.py` correctly leaves the brownfield entry alone -- and `trailing-whitespace`
   and `end-of-file-fixer` then run twice on every commit, with nothing said about it.** This
   is deliberate on the fragment's side (`repo: builtin` is faster and needs no clone or rev),
   so the fix is a warning, not a merge: `merge_hooks.py` now reports any hook id that appears
   under both a fragment-owned repo and a repository-owned one.
   `tests/test_scaffold.py::test_a_hook_id_the_repository_already_ran_gets_a_duplicate_warning`.
4. **The `just` layer's own justfile -- `setup`, `check`, `each`, and the rest -- is skipped
   whole when a justfile already exists, so a repository whose own `setup` or `check` predates
   the scaffold now shadows the built-in recipe of the same name.** `just check` runs only the
   repository's own recipe; the built-in one that runs `hooks-all` and every language's own
   check is simply never placed, and CI calling the same name gets the same silence. Fixed by
   having `gen_justfile.py` warn when the justfile (wherever it came from) defines a name the
   layer's own justfile also defines. `tests/test_scaffold.py::test_a_recipe_name_the_scaffold_also_wants_is_a_warning`.

Both (3) and (4) had a second bug once fixed: **the warning went quiet on a second apply.**
`merge_hooks.py` tracks per-hook ownership to decide who wins a real conflict, but the
tracking was only updated when a fragment's hook *changed* something; when a re-applied
fragment's value matched what was already on disk (itself, from the first apply) the early
`continue` skipped the ownership update, so the second run's duplicate check no longer saw
`builtin` as fragment-owned. `gen_justfile.py`'s collision check lived only in the
"no import block yet" branch, which by construction runs exactly once -- the second apply
takes the "block already present, rewrite between the markers" branch and never re-checked.
Both are standing properties of the file, not one-time transitions, and both are fixed the
same way: check on every run, and keep ownership correct even when nothing changes.
`tests/test_scaffold.py::test_the_duplicate_hook_warning_survives_a_reapply` and the second
`scaffold()` call inside `test_a_recipe_name_the_scaffold_also_wants_is_a_warning`. Found by
applying the fixture twice by hand and diffing the warning lists -- the property test suite
scaffolds once per case, so neither gap had a test until this round.

Checked and found correct, no fix needed: `.gitignore` and `justfile` merges keep every
hand-written line (`fold_gitignore.py`, `gen_justfile.py`'s append path); a dirty
uncommitted change in `src/index.ts` survived byte-for-byte, because nothing in the TS
layer writes there; `tsconfig.json`, `biome.json`, and `README.md` are correctly classified
`overwrite` with an accurate `lines_lost` count -- they carry `@@` tokens (`AWS_CDK_DEST`,
`BIOME_VERSION`, `PROJECT_NAME`), so `SKIP_IF_EXISTS` cannot apply, and `plan` says so before
`apply` does it; a hand-owned `.github/workflows/ci.yml` is left alone with the existing
`gen_caller` warning naming the orphaned `wc-*.yml` files; re-applying the fully-collided
fixture a second time changed zero bytes (`create/overwrite/merge/remove` all `0`) with the
same six warnings repeated verbatim, not merely "still ok".

`lines_lost` counts raw text lines, not semantic content: `.pre-commit-config.yaml` shows
`lines_lost: 9` for a 9-line brownfield file even though every hook in it survives, because
`merge_hooks.py` re-serialises the whole file through `yaml.dump`, changing every line's
formatting. Nothing is actually lost; `plan --json` has no way to say that short of a
schema-aware diff for one file type, which would cut against "measure the real difference,
never special-case a file type." Left alone -- recorded here because `lines_lost` is exactly
the number a caller is told to trust, and for this one file it overstates the loss.

## Open, recorded rather than guessed at

1. **A rejected answer ends the interview instead of re-asking.** Copier validates a `str`
   answer after the prompt returns, not inline, so it raises and the interview stops. The
   invalid value reaches no file and the user is told why, which is the important half, but
   a typo in `PROJECT_NAME` costs the whole conversation. Settled by deciding whether the
   validator should move into the prompt, where questionary can re-ask, and whether that is
   worth losing Copier's own single source for the rule. Covered as measured behaviour by
   `test_an_invalid_name_stops_the_interview_and_records_nothing`.

2. **The validation-width budget measures the wrong thing.** Copier prefixes every message
   with `Validation error for question 'X': ` -- 45 characters before ours begins -- so at
   80 columns a 71-character rule wraps rather than truncating, and it wraps mid-word. No
   message needs shortening, because wrapping is not truncation, but
   `test_a_rejection_message_fits_an_eighty_column_terminal` is asserting a narrower
   property than its name claims. Either rename it or budget for the prefix.

## What the eighth round fixed

Both of the seventh round's targets, plus what looking at them turned up.

1. **The journey gate was flaky.** Retries are now scoped to the three network-bound
   steps and matched on transport symptoms only, never a tool's verdict, and a retry that
   succeeded is named in the summary. Six consecutive full runs since: 12 walked, 0 failed,
   0 retries needed.
2. **A monorepo member carried the root's surface.** `--member` applied the right layers
   but still wrote 8 fragment files with no consumer beside them, and both `biome.json` and
   `.oxlintrc.json`, which biome and oxlint each refuse as a nested duplicate: the root's
   own `just check` failed the moment a member existed. A member is 7 files now, from 69.
3. **A member of a member registered in the wrong root.** Any answers file counted as a
   root marker, so a grandchild wrote into the member's own orphaned `.ci/members.json`
   and reported success. A member-scoped write is marked, and the search keeps climbing.
4. **A member whose language the root lacks was accepted.** Registered, reported success,
   and surfaced later as an unrelated generator failure naming `.ci/members.json`. Refused
   up front now, naming both fixes.
5. **`just --list` showed fragments of rationales as descriptions.** `just` takes only the
   last comment line above a recipe, and several put the summary first: `check` read
   "success having edited the tree it was asked to inspect." One of those comments was
   added earlier in the same round, so the convention was documented and still not applied.
6. **The gate stopped at the monorepo shell.** It now scaffolds every member
   `.ci/members.json` names and re-runs the root's journey. Proven to catch what it exists
   for: with the two excludes removed it reports FAIL and names biome's own failing line.
   Its language mapping immediately earned a test by missing rust.
7. **`--member` was undocumented in SKILL.md**, so an agent following it would hit
   NESTED_SCAFFOLD with no way to learn the fix lives in a flag.

Looked at and found nothing: **deselecting a layer**. `STALE_LAYER_FILES` already fires
from validate, plan and apply, names the count, lists the files and explains that
gen_caller and gen_justfile wire from what is on disk rather than from the answer. The
first test of it reported a false negative because the destination's answers file had
already recorded the new value, which is the flawed-setup mistake this document warns
about, from the other direction.

## What the seventh round fixed (journey gate flakiness)

Only the first of the round's two targets: making the journey gate deterministic. The
monorepo member-scoped apply is untouched.

On 2026-09-26, seven full `tools/e2e.py` runs came back 12 walked, 0 failed: three
before this fix, four after. The flakiness would not reproduce live.

Every cache the journey touches was already warm from prior rounds: mise's tool
installs, `~/.cargo/registry`, `uv`'s cache, bun's, and `~/.cache/prek`'s four cloned
hook repos. The network itself answered every check. `curl` against npm, crates.io,
PyPI, and GitHub all succeeded. Only `proxy.golang.org` failed to resolve, and nothing in
the journey talks to it. Forcing a cold run would mean deleting those caches, and they
are shared across every project on the machine, not scoped to this repository. That path
was rejected as disproportionate, once the network-bound commands were identifiable
directly from the recipes.

So the fix targets the actual network surface instead of a reproduced failure:

- `just setup` fetches the toolchain and every language's locked dependencies.
- `just check` runs `hooks-all` first, which on a cold `prek` cache clones four remote
  hook repos over https before anything local runs: betterleaks, typos, shellcheck-py,
  and conventional-pre-commit, all declared in
  `assets/hooks/.pre-commit.d/hygiene.yaml.template`.
- `just aws-cdk-init` downloads the CDK CLI through `bunx`, then `bun install`s the app
  it generates.

Because a failed attempt leaves no partial state, all three fail closed and a retry
never trips over a previous attempt's leftovers: `aws-cdk-init` only `os.replace`s its
temp directory into place once both subprocesses succeed. `tools/e2e.py` now retries
each step up to twice, with a short backoff. It retries only when the failed attempt's
own stdout or stderr names a transport-level symptom. `NETWORK_TRANSIENT` matches DNS
failures, connection resets, timeouts, TLS handshake timeouts, and a registry's own 429.
It never matches a tool's verdict about the code, so a lint or test failure repeats
identically on every attempt and a retry never turns it into a false pass. That regex is
what keeps this from being "retry everything," which would satisfy the brief's letter
while quietly widening what the gate excuses. `stage`, `commit`, and `clean` touch no
network, so `tools/e2e.py` never retries them: retrying a deterministic failure only
delays reporting it.

A retry is not a skip. The preset still walks its full journey, but the run still hit a
real transient symptom, so the summary names it rather than folding it silently into
`ok`: `N preset(s) needed a retry for a transient network symptom`, one line per step.
No step in the current journey gets a new skip disposition from this change.
`journey_skip_reason` already covers the one skip case that exists, a toolchain apply
reporting a tool absent, and nothing found on 2026-09-26 needed a second one.

`tests/test_e2e_journey.py` covers four cases: a transient failure that succeeds on
retry and gets reported, retries exhausting with the attempt count named in the failure
message, a deterministic failure that a retry-allowing call never retries, and the
`NETWORK_TRANSIENT` regex matching real transport-symptom strings while rejecting a
tool's own verdict text.

Judgement call: this round investigated `go-vuln`'s dead-proxy handling from the sixth
round as a candidate cause, then ruled it out. It sits in neither `just check`'s
aggregate (`go: go-fmt-check go-lint go-test`) nor any pre-commit hook. The brief's
mention of it describes the state before the sixth round's fix, not a live gap.

## Rules

- **Do not `git push`.** Commit locally, on a branch if the change is large.
- Do not hand-edit `templates/`; change `assets/` or `tools/port_assets.py` and re-port.
- Do not edit a generated file in a scaffolded output either — `rule://project-setup-generated-file-guard`
  will stop you, and it is right.
- Add a test for every fix. If it was worth fixing it was worth pinning.
- The `omp-plugins` repo has an unpushed `chore/retire-project-setup` branch and unrelated
  uncommitted work by the user. Leave both alone.

## Report back

For each finding: what you did, what happened, what a user would have expected, what you
changed, and the test that now covers it. Separate real defects from judgement calls, and
say plainly where you disagreed with an existing decision and why.
