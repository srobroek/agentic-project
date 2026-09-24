# Brief: shake out `project-setup`

You are picking up a deterministic repository scaffolder. Two shake-out rounds have closed the
obvious defects, so this round is about **improvement**: propose changes and make them. Not
only crashes — friction, needless questions, inflexibility with no reason behind it, and
places where the tool is merely adequate.

Read `README.md` for what it is, `AGENTS.md` for the invariants, and
`skills/project-setup/SKILL.md` for how an agent is meant to drive it.

## This round: four named targets, then your own judgement

Do these four first, because each is a known gap rather than a hunch. Then range wider.

### 1. Make `plan` provably honest

`plan` is the only warning a user gets before files are written, and it has lied twice: it
listed the justfile as both overwritten and merged, and promised "replaced outright" for a
file it would not touch. Both were found by hand.

Replace the spot checks with a **property**: for every stack, and every disposition a file can
have, assert that what `plan` reports is what `apply` actually does. Run `plan`, run `apply`,
diff the claim against the result. A mismatch is a defect wherever it is.

### 2. Decide what an interrupted `apply` leaves behind

There is no handling and no test: `grep -r 'resume\|partial\|interrupt' src/ tests/` is empty.
Kill an `apply` mid-run — between layers, and between generators — and answer plainly: what
state is the repository in, can the user tell, and can they recover by re-running? Then make
the answer acceptable. Re-running is already idempotent, which may be most of the work; if so,
prove it and say so rather than adding machinery.

### 3. Remove the per-call flag friction

Outside a source checkout the CLI needs `--templates` and `--presets` on **every** invocation,
because the templates belong to the plugin and are deliberately not bundled (see AGENTS.md).
An agent must therefore thread two paths through every command, and a human must export two
variables. That is a tax on the common case. Find a way to make the common case free without
reintroducing the staleness the no-bundling rule exists to prevent.

### 4. Exercise the two paths nothing has driven

- `project-setup interview --dest .` — the interactive prompt path. No automated check drives a
  TTY, so this is the least-verified surface in the repo. Judge the question count, order, and
  whether deselecting a layer really silences its questions.
- `tools/tasks/init_aws_cdk.py` — shipped, never executed. Needs `cdk` on PATH.

## Prove it still works before you change anything

```sh
just port                          # regenerate templates/ from assets/
.venv/bin/python -m pytest -q      # 201 unit tests
.venv/bin/python tools/e2e.py      # 12 stacks, end to end, with tasks
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
   `PROJECT_SETUP_TEMPLATES`, then a source checkout, then an error naming all three.
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
