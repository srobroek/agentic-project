# project-setup

Deterministic, layered repository scaffolding. Copier renders; this repo owns the layers and
the question set.

| | |
| --- | --- |
| Engine | Copier 9.18, driven in-process |
| Layers | 17 under `templates/` — one directory per capability |
| Question set | `templates/_interview/` — generated from the layers |
| Shapes | `presets/*.yml` |
| Agent entry point | `skills/project-setup/SKILL.md` |
| OMP plugin | `@srobroek/project-setup` |

## Why layers instead of one big template

One template with N conditionals makes complexity multiplicative: every new choice adds
branches to a shared config. One directory per capability makes it additive. A new choice is a
new directory, and nothing shared changes.

## Install

    mise install
    uv pip install -e .

## Use it without an agent

    project-setup presets
    project-setup apply --preset ts-service --dest ../my-app \
      --set PROJECT_NAME=my-app --set DESCRIPTION="Thing that does X" \
      --set CODEOWNER=@me --set SECURITY_CONTACT=security@example.com

Or answer the prompts yourself — Copier owns the question set, so there is no second
implementation to drift:

    project-setup interview --dest ../my-app

It finishes by printing the rest of the pipeline with `--dest` and the answers path already
filled in, because `apply` defaults `--dest` to the working directory and a command missing
it scaffolds wherever you happen to be standing.

    project-setup validate --data-file ../my-app/.project-setup-answers.yml --dest ../my-app
    project-setup plan     --data-file ../my-app/.project-setup-answers.yml --dest ../my-app
    project-setup apply    --data-file ../my-app/.project-setup-answers.yml --dest ../my-app

## Commands

| Command | Purpose |
| --- | --- |
| `catalog [--json]` | every layer and the questions it declares |
| `presets [--json] [--show NAME]` | standard project shapes |
| `interview --dest D` | ask the questions, write an answers file, print the next command. No model |
| `validate [--json]` | check an answer set, including every declared validator. Writes nothing |
| `plan --dest D [--json]` | dry run. Writes nothing, and names every file it would overwrite or fold into |
| `apply --dest D [--json]` | scaffold, run tasks, run generators |

Answer sources compose, later winning: `--preset`, then `--data-file`, then `--set KEY=VALUE`.

`plan` is the only warning before an existing file is replaced, because Copier overwrites by
default. It counts what it would create, lists what it would overwrite by name, and lists
separately the shared files the generators fold into — Copier never places those, so its own
per-file output cannot mention them:

    61 file(s) to create, 2 to overwrite

    2 existing file(s) would be overwritten:
      CONTRIBUTING.md
      README.md

    5 existing path(s) a generator would rewrite:
      .gitignore                merged, your entries kept
      .pre-commit-config.yaml   merged, your entries kept
      justfile                  merged, your entries kept
      .github/workflows/ci.yml  left alone: yours, no generator marker
      CLAUDE.md                 replaced outright

Three dispositions, not two: `gen_caller.py` will not touch a `ci.yml` it did not write, and
a plan that promised to replace one threatened something that never happens. The justfile is
on the second list only, because the `just` layer skips an existing one and the generator
folds its import block into the file you already had.

`apply` ends with two lists: answers still carrying a placeholder, and any step that did less
than the full job. The second is how a missing toolchain surfaces, since a skipped
`cargo init` does not fail the scaffold:

    1 step(s) did less than the full job:
      lang-rust  native_init: cargo is not on PATH, so Cargo.toml was not created. …

## Where the templates come from

The templates are the plugin's payload, not the CLI's, and are deliberately **not** bundled
into the wheel: the plugin can be upgraded on its own, and a bundled copy would go stale
without saying so. So the CLI has to be told where they are:

| Precedence | How |
| --- | --- |
| 1 | `--templates DIR` / `--presets DIR` |
| 2 | `PROJECT_SETUP_TEMPLATES` / `PROJECT_SETUP_PRESETS` |
| 3 | a source checkout, when running from one |

Running from this repository, none of that is needed. Installed from a plugin, ask OMP where
the plugin lives:

    PLUGIN=$(omp plugin list --json | python3 -c '
    import json, sys
    for e in json.load(sys.stdin).get("npm", []):
        if e.get("name", "").endswith("/project-setup"):
            print(e["path"]); break')
    project-setup --templates "$PLUGIN/templates" --presets "$PLUGIN/presets" catalog

If none of the three resolve, the CLI fails naming all three rather than guessing. A wrong
guess scaffolds from the wrong layer set.

## How a scaffold runs

1. **place** — each selected layer is rendered by Copier. `.jinja` files are rendered; every
   other file is copied byte-for-byte.
2. **tasks** — Copier's own `_tasks`: `git init`, materialise `LICENSE` from the bundled SPDX
   texts, or none of them when `SPDX_ID` is `NONE`; write one file per ADR; let
   `bun`/`cargo`/`uv` own their manifests. Each is idempotent and degrades to a warning when
   its tool is absent, and `apply` prints those warnings: a Rust scaffold with no `Cargo.toml`
   used to report `place ok lang-rust` and exit 0.
3. **prune** — directories left empty because their contents were excluded are removed.
4. **generate** — the `.d/` fragments each layer dropped are folded into shared destinations:

| Fragments | Generator | Destination |
| --- | --- | --- |
| `.gitignore.d/` | `fold_gitignore.py` | `.gitignore` managed block |
| `.pre-commit.d/` | `merge_hooks.py` | `.pre-commit-config.yaml` |
| `.just.d/` | `gen_justfile.py` | `justfile` import block |
| `.github/quality.d/`, `security.d/` | `gen_caller.py` | `.github/workflows/ci.yml` |
| the whole tree | `gen_steering.py` | `docs/agents/` |
| `docs/agents/AGENTS.body.md` | `install_agents_index.py` | `AGENTS.md`, `CLAUDE.md` |

Step 4 is why layers can overlap without any layer owning a shared file. The CI caller is
*derived from the tree*: add a language layer and the workflow graph gains its jobs.

## Presets: stacks and parts

A **part** carries one concern: a language toolchain, a forge, release automation. A **stack**
is a composition of parts. Nothing prevents composing them yourself, so a shape with no stack
of its own is still reachable:

    project-setup presets                       # stacks, and the parts beneath them
    project-setup presets --show desktop-rust-ts   # what it resolves to, and from where
    project-setup apply --preset parts/lang-rust --preset parts/lang-ts ...

`--preset` is repeatable and later presets win. A stack lists its bases under `_extends`,
resolved depth-first, so the stack always overrides the parts it builds on. Cycles are refused.

## Two required answers, and placeholders for the rest

Only `PROJECT_NAME` and `DESCRIPTION` have no default. A production URL, an owner, a security
contact — things nobody knows while setting up — carry an obvious placeholder rather than
blocking the scaffold, and `apply` lists them when it finishes:

    2 answer(s) still carry a placeholder:
      CODEOWNER         @TODO-owner
      SECURITY_CONTACT  security@example.com

## Layers

| Always applied | Opt-in |
| --- | --- |
| `base` `governance` `hooks` `just` `ci` `forge` `steering` | `release` `worktrunk` `api` `i18n` `a11y` `infra-aws-cdk` `lang-go` `lang-python` `lang-ts` `lang-rust` |

61 distinct questions across all layers, only the selected layers' are asked, and only two are
ever required — `PROJECT_NAME` and `DESCRIPTION`. A minimal project answers ten prompts,
because four classes of question are not asked by default. `catalog --json` reports which class
each one is in, and `--set` reaches every one of them without the gate:

| Class | Asked | Marked | Why |
| --- | --- | --- | --- |
| tool version | behind `PIN_TOOL_VERSIONS` | `"pinned": true` | a tested set Renovate bumps |
| shipped default | behind `CUSTOMISE_DEFAULTS` | `"tuned": true` | a hook limit, a job timeout, the README's install line |
| composed | never | `"composed": true` | a JSON artifact nobody types at a prompt: the ADR list, the monorepo members |
| derived | never | `"derived": true` | a pure function of another answer |

The layer selection is one multiselect, not one yes/no per layer, and each choice says what
the layer does rather than only naming its directory. `WANT_<LAYER>` stays the answer a
preset, a data file and `--set` write, and it is derived from the list when the interview
asks. `project-setup catalog` lists the same one-line purpose for all seventeen.

Each language layer hands its manifest to the language's own tool — `cargo init`, `bun init`,
`uv init`, `go mod init` — and then reconciles what that tool leaves behind against what the
layers own. A fresh scaffold of any stack passes its own `just setup` and `just check`.

`FORGE_PLATFORM` is a single answer that swaps the entire CI surface: choose `gitlab` and every
`.github/` file from every layer is excluded, the `.gitlab/ci` fragments are used instead, and
`gen_caller.py` reports there is no caller to write. Excluded, not deleted — so changing the
answer in a repository that already has one forge's workflows leaves them in place and still
triggering. `validate`, `plan` and `apply` all report that as `STALE_FORGE_SURFACE`, naming
the files; removing somebody's CI is not this tool's call.

## Measured

| | |
| --- | --- |
| 17 layers + 6 generators + tasks | **4.9 s CPU** |
| Re-apply | byte-identical, 0 changes |
| Unresolved tokens | 0 |
| Empty directories | 0 |
| Tests | 197 unit + 12 presets end to end |
| Fresh scaffold | `just setup` and `just check` green, all four languages |

CPU time rather than wall clock, because wall clock tracks machine load.

## Using it from OMP

This repository is also an OMP plugin. It ships a skill, a slash command and a rule; the CLI
is the Python package in the same tree.

```sh
just omp-link          # omp plugin link . && omp plugin doctor
```

| Capability | Path | Addressed as |
| --- | --- | --- |
| Skill | `skills/project-setup/SKILL.md` | `skill://project-setup` |
| Command | `commands/project-setup.md` | `/project-setup` |
| Rule | `rules/project-setup-no-handcopy.md` | `rule://project-setup-no-handcopy` |

Every capability name is prefixed `project-setup`, because OMP deduplicates capability names
across all configured sources and keeps the first match: a shared name silently hides one
plugin.

## Regenerating the templates

    just port

`assets/` is the source of truth and is vendored here; `templates/` is generated from it.
Edit the assets or `tools/port_assets.py`, never the output. The port fails loudly rather
than emitting a question that renders blank: an undeclared token, an unmapped optional
block, an asked question with no help text, or a question that reads an answer asked later.
