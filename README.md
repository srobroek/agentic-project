# project-setup

Deterministic, layered repository scaffolding. Copier renders; this repo owns the layers and
the question set.

| | |
| --- | --- |
| Engine | Copier 9.18, driven in-process |
| Layers | `templates/<layer>/` — one directory per capability |
| Question set | `templates/_interview/` — generated from the layers |
| Shapes | `presets/*.yml` |
| Agent entry point | `skills/project-setup/SKILL.md` |

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

## How a scaffold runs

1. **place** — each selected layer is rendered by Copier. `.jinja` files are rendered; every
   other file is copied byte-for-byte.
2. **tasks** — Copier's own `_tasks`: `git init`, materialise `LICENSE` from the bundled SPDX
   texts, and let `bun`/`cargo`/`uv` own their manifests.
3. **generate** — the `.d/` fragments each layer dropped are folded into the shared
   destinations (`.gitignore`, `.pre-commit-config.yaml`, the justfile import block).

Step 3 is why layers can overlap without any layer owning a shared file.

## Measured

| | |
| --- | --- |
| Place 6 layers + generate | **2.2 s** |
| Re-apply | byte-identical, 0 changes |
| Unresolved tokens | 0 |

## Regenerating the templates

    just port ../omp-plugins/project-setup/skills/project-setup/assets

`templates/` is generated. Edit the assets or `tools/port_assets.py`, never the output.
