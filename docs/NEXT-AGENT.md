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
just port          # regenerate templates/ from assets/
pytest -q          # 67 unit tests
python3 tools/e2e.py   # all 12 stacks, end to end, with tasks
```

`tools/e2e.py` asserts, per stack: validate is clean, `plan` writes nothing, `apply`
succeeds, no `@@` tokens survive, expected files exist, excluded files do not, no empty
directories, a re-apply changes zero bytes, and the answers file is recorded.

Re-run all three after every change. A change that breaks `e2e.py` is a regression.

## What has genuinely never been tested

1. **The interactive interview.** `project-setup interview --dest .` prompts, and no
   automated check drives a TTY. Run it. Does it ask a sane number of questions, in a sane
   order? Does deselecting a language really stop it asking about that language?
2. **The agent interview.** `cd` somewhere empty, run `omp`, then `/project-setup`. This is
   the main user path and the least verified.
3. **Brownfield.** Apply into a repo that already has real tooling — its own
   `.pre-commit-config.yaml`, its own `.gitignore`, its own `justfile`. What gets clobbered?
   What should have been asked about and was not?
4. **`init_aws_cdk.py`** is shipped but has never been executed. Needs `cdk` on PATH.

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

## Known constraint, possibly worth changing

Changing an answer that is already merged into a shared file is **refused**, not overwritten:
`COMMIT_SCOPES` has been folded into `.pre-commit-config.yaml`, so a new value reports a
conflict, and the recovery is to delete the generated file and re-apply. That is safe but
poor. If you can make it re-derive cleanly instead, that is a real improvement — see
`tests/test_scaffold.py::test_changing_a_merged_answer_is_refused_not_silently_applied`.

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
