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

## Verifying a change

    just port ../omp-plugins/project-setup/skills/project-setup/assets
    just catalog
    project-setup apply --preset polyglot-service --dest /tmp/check --set ...
    # then apply again: the tree must be byte-identical
