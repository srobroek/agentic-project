# Brief: shake out `project-setup`

You are picking up a deterministic repository scaffolder. Two shake-out rounds have closed the
obvious defects, so this round is about **improvement**: propose changes and make them. Not
only crashes — friction, needless questions, inflexibility with no reason behind it, and
places where the tool is merely adequate.

Read `README.md` for what it is, `AGENTS.md` for the invariants, and
`skills/project-setup/SKILL.md` for how an agent is meant to drive it.

## This round: four named targets, then your own judgement

Do these four first, because each is a known gap rather than a hunch. Then range wider.

### 1. Make the checks run the journey, not just the scaffolder

Every defect found in the last round was found by hand, by running the sequence a user
runs — `apply`, then `just setup`, then `just check` — and none of them was caught by
`pytest` or `tools/e2e.py`, which both passed throughout. That is the gap.

Fold the journey into `tools/e2e.py`: for each preset, after `apply`, run `just setup` and
`just check` in the scaffolded repository and require both to exit 0. Where a language
toolchain is missing, skip with a reported reason rather than passing silently. This is the
single highest-value change available, because it converts the thing that keeps finding bugs
from a manual habit into a gate.

A fresh scaffold currently reaches `setup=0 check=0` on minimal, go-service, ts-service,
rust-cli, py-lib, web-app and fullstack-web, and on fullstack-web again after
`just aws-cdk-init`. That is the baseline to lock in.

### 2. Drive the interactive interview

`project-setup interview --dest .` prompts, and nothing automated drives a TTY, so it is
still the least-verified surface in the repo. It can be driven: `pty.fork`, write
keystrokes, read the answers file. That technique found the keypress leak — a `bool` was a
confirm, which submits on one keypress, so the Enter a user typed after `y` silently
answered the next question. All five asked bools are selects now; a test should hold that
and check the question order and count.

### 3. Verify the layers nothing has exercised end to end

`just i18n`, `just api`, and the monorepo member CI path have never been run in a
scaffolded repository. `just a11y` had never run either, and turned out to have never
worked at all: its Playwright config used an ESM-only API that its own loader cannot take.
Assume the same of anything unexercised.

### 4. Keep one source of truth for a version

Thirteen tool versions were pinned in both `TOKEN_POLICY` and the preset parts, and the
preset silently won — so bumping the policy did nothing and a fresh Go scaffold failed its
own lint with golangci-lint built against an older Go than the pinned toolchain. Versions
now live only in `TOKEN_POLICY` and a test forbids restating one. Check the pinned set is
mutually compatible, not merely present: several pins are months stale.

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
   generators were about to rewrite. Both fixed.
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
