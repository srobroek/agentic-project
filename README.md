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
    project-setup apply --data-file ../my-app/.project-setup-answers.yml --dest ../my-app

## Commands

| Command | Purpose |
| --- | --- |
| `catalog [--json]` | every layer and the questions it declares |
| `presets [--json] [--show NAME]` | standard project shapes |
| `interview --dest D` | ask the questions, write an answers file. No model |
| `validate [--json]` | check an answer set. Writes nothing |
| `plan --dest D [--json]` | dry run. Writes nothing |
| `apply --dest D [--json]` | scaffold, run tasks, run generators |

Answer sources compose, later winning: `--preset`, then `--data-file`, then `--set KEY=VALUE`.

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
   texts, write one file per ADR in the manifest, and let `bun`/`cargo`/`uv` own their
   manifests. Every task is idempotent and degrades to a warning when its tool is absent.
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

## Layers

| Always applied | Opt-in |
| --- | --- |
| `base` `governance` `hooks` `just` `ci` `forge` `steering` | `release` `worktrunk` `api` `i18n` `a11y` `infra-aws-cdk` `lang-go` `lang-python` `lang-ts` `lang-rust` |

58 distinct questions across all layers, but only the selected layers' questions are asked, and
only four are ever required: `PROJECT_NAME`, `DESCRIPTION`, `CODEOWNER`, `SECURITY_CONTACT`.

`FORGE_PLATFORM` is a single answer that swaps the entire CI surface: choose `gitlab` and every
`.github/` file from every layer is excluded, the `.gitlab/ci` fragments are used instead, and
`gen_caller.py` reports there is no caller to write.

## Measured

| | |
| --- | --- |
| 14 layers + 6 generators + tasks | **4.9 s CPU** |
| Re-apply | byte-identical, 0 changes |
| Unresolved tokens | 0 |
| Empty directories | 0 |
| Tests | 51 unit + 10 presets end to end |

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

The plugin is named `project-setup` rather than `project-setup` because OMP deduplicates
capability names across all sources and keeps the first match. The older
`project-setup@srobroek-omp` plugin still claims that name; once it is retired this can take it.

## Regenerating the templates

    just port ../omp-plugins/project-setup/skills/project-setup/assets

`templates/` is generated. Edit the assets or `tools/port_assets.py`, never the output.
