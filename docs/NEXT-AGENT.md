# Brief: shake out `project-setup`

You are picking up a deterministic repository scaffolder that works but has never been used
in anger. Your job is to **use it like an impatient user would, find where it fights back,
and fix that** — not just crashes, but friction, needless questions, and inflexibility that
has no good reason behind it.

Read `README.md` for what it is, `AGENTS.md` for the invariants, and
`skills/project-setup/SKILL.md` for how an agent is meant to drive it.

## Where things are

| | |
| --- | --- |
| Repo | `/Users/sjors/personal/dev/project-setup` (6 commits, clean, **never pushed**) |
| CLI | `project-setup`, already on PATH, editable install pointing at this repo |
| OMP plugin | `@srobroek/project-setup@0.1.0`, symlinked, so edits are live |
| Slash command | `/project-setup` |
| `assets/` | source of truth for file content |
| `templates/` | **generated** from `assets/` by `tools/port_assets.py`. Never hand-edit |
| `presets/` | 12 stacks, plus `presets/parts/` with 12 single-concern parts |

## Prove it still works before you change anything

```sh
just port                          # regenerate templates/ from assets/
.venv/bin/python -m pytest -q      # 176 unit tests
.venv/bin/python tools/e2e.py      # all 12 stacks, end to end, with tasks
```

Run them through the project interpreter. A bare `pytest` resolves to the mise shim, which
cannot import `project_setup`, and every test file fails to collect. `just check` and
`just e2e` do this for you.

`tools/e2e.py` asserts, per stack: validate is clean, `plan` writes nothing, `apply`
succeeds, no `@@` tokens survive, expected files exist, excluded files do not, no empty
directories, a re-apply changes zero bytes, and the answers file is recorded.

Re-run all three after every change. A change that breaks `e2e.py` is a regression.

## What had never been exercised, and what it found

All four have now been driven for real. What each one produced is recorded here so the next
reader does not repeat it.

1. **The interactive interview.** Driven through a PTY at 80, 120 and 200 columns. 27 prompts
   for the most minimal project, in a sane order, and deselecting a language does stop its
   questions. Nine prompts were truncated mid-word by the terminal width; fixed and now
   enforced by `_check_help_fits_a_prompt`. Its closing line named an `apply` command with no
   `--dest`; the CLI prints the whole pipeline instead. There is still no TTY driver in the
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
4. **Never ask for a `derived` value.** `catalog --json` marks them; `derived_from` holds the
   expression, which is not a value to pass through.
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

## Found, judged, and deliberately left alone

None of these was fixed. Each is recorded with its file and its symptom so the finding
survives, and each was left because the trade belongs to whoever owns the design, not
because it went unnoticed. This repository has no ledger, so this list is the carrier.

1. **`tools/port_assets.py` — the layer selection is ten separate yes/no prompts.** A
   minimal project answers 27 questions and 10 of them are `Include the <layer> layer?`. A
   Copier `multiselect` would be one. It changes `WANT_*` from booleans to a list, which
   every preset, `selected_layers` and `want_var` read. Check: one prompt selects the layers
   and `presets --show` still resolves each stack to the same layer list.
2. **`tools/port_assets.py` — eight always-asked questions are knobs with correct
   defaults.** `MAX_FILE_KB`, `JOB_TIMEOUT_MINUTES`, `HOOK_EXCLUDE_PATTERNS`,
   `COMMIT_SCOPES`, `CODE_OF_CONDUCT_CONTACT`, `INSTALL_COMMANDS`, `USAGE_EXAMPLE`, `ADRS`.
   `PIN_TOOL_VERSIONS` is the precedent for putting a set behind one gate. Not applied,
   because none of them is derivable, task-decided or preset-fixed, so none meets the bar
   the rest of this document sets. Check: a plain project answers fewer than twenty prompts
   and every knob is still reachable without editing the answers file.
3. **`tools/port_assets.py` — `ADRS` and `MONOREPO_MEMBERS` are prompted as raw JSON.**
   `SKILL.md` calls them artifacts assembled from the conversation, which is exactly what a
   human at a one-line prompt cannot do. Dropping them from `_interview` would shorten it
   and remove an unanswerable question, at the cost of making a monorepo inexpressible
   without a model. Check: `interview` never prompts for a JSON array, and `--set` and a
   data file still carry both.
4. **`src/project_setup/catalog.py` — deselecting a layer leaks its files.** Re-applying
   with `WANT_RELEASE: false` leaves the release layer's files in place, exactly as the
   forge switch did before `_stale_forge_surface`. Detecting it needs a per-layer file
   manifest, which nothing currently builds. Check: re-applying with a layer removed reports
   the files that layer wrote and which are still present.
5. **`templates/_interview/copier.yml` — `FORGE_PLATFORM` is asked 27th of 27.**
   `SKILL.md` says to ask it "at once, early", because it swaps the whole CI surface. Its
   position follows layer order, `forge` after `ci`. Check: it is asked with the layer
   selection, and `_check_declaration_order` still passes.
6. **`catalog --json` no longer carries the `ADRS` schema.** It moved out of `help` to fit
   an 80-column prompt, and now lives only in `skills/project-setup/SKILL.md`. A non-agent
   reader of `catalog --json` has one more hop. The alternative is a `detail` field the port
   keeps out of the interview: more machinery, and a second string per token to drift.
   Check: the schema is reachable from `catalog --json` without lengthening any prompt.
7. **`README.md`, `AGENTS.md`, `SKILL.md`, this file — `uvx slopvac` fails on all four, and
   did so before any of this work.** Measured on the previous commit: the same 71.4 score and
   the same failure classes, Unicode dashes and the `prose-format` budget. The em dash is this
   repository's own markdown style, so the fix is a deliberate style change across every
   document, not a patch to whichever paragraph was edited last. Check: `uvx slopvac` passes
   on all four, and the Python comments still read the way they do now.

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
