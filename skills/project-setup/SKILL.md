---
name: project-setup
description: Scaffolds a repository from layered Copier templates. Use when setting up a new project, or adding a capability layer to an existing one. The templates own the questions; you only choose layers and supply answers.
---

# Project Setup

TRIGGER
+ "set up a project", "scaffold this repo", "add CI/hooks/a language layer"
+ a new repository with no tooling, or an existing one missing a layer
- changing one tool's config in a repo already set up → that tool's own skill
- authoring a new layer → `references/authoring.md`

## First, decide whether you are needed at all

The scaffold is deterministic and runs without a model. Check this before doing anything:

| Situation | What to do |
| --- | --- |
| Greenfield, fits a preset | **Tell the user the one command and stop.** Do not conduct an interview |
| Greenfield, no matching preset | Compose a data file (below) |
| Brownfield (tracked files exist) | Classify first, then compose a data file |
| Answers that must be *composed*, not chosen | Compose a data file |
| After the scaffold lands | Your real work begins: code, libraries, layout |

MUST offer the no-agent path first when a preset fits. It is faster, reproducible, and
costs nothing:

```
project-setup apply --preset ts-service --dest . \
  --set PROJECT_NAME=my-app --set DESCRIPTION="..." \
  --set CODEOWNER=@me --set SECURITY_CONTACT=security@example.com
```

## Never ask a question the templates do not declare

The template layers are the question set. Read it, never invent it:

```
project-setup catalog --json      # every layer, every question, types, choices, defaults
project-setup presets --json      # standard shapes
```

MUST NOT ask for a value that `catalog --json` does not list. If you believe a question is
missing, the answer is a **missing layer**, not a missing question — say so.

MUST NOT ask for a value a preset already fixes. Presets exist to collapse the interview.

## Building the data file

1. **Start from the closest preset.** Copy it; do not restate it.
2. **Add the four identity answers** no preset can carry: `PROJECT_NAME`, `DESCRIPTION`,
   `CODEOWNER`, `SECURITY_CONTACT`.
3. **Select layers** by setting `WANT_<LAYER>: true`. `catalog --json` lists the exact names.
4. **Validate before writing anything.**

```
project-setup validate --data-file answers.yml --json
```

The response is structured. `MISSING_REQUIRED` names the question and the layers that need
it. `INVALID_CHOICE` lists the permitted values. `UNKNOWN_KEY` means a typo or an unselected
layer. Fix and re-validate; it writes nothing.

5. **Show the plan and wait.** `plan` is a dry run and mutates nothing:

```
project-setup plan --data-file answers.yml --dest . --json
```

6. **Apply** only after the user approves the plan.

```
project-setup apply --data-file answers.yml --dest . --json
```

## Composed answers — the part only you can do

Most answers are choices. A few are artifacts that must be assembled from the conversation,
and these are the reason to involve a model at all:

| Answer | What it needs |
| --- | --- |
| `INSTALL_COMMANDS`, `USAGE_EXAMPLE` | derived from the accepted stack |
| `COMMIT_SCOPES` | the project's real module names |
| `HOOK_EXCLUDE_PATTERNS` | paths that genuinely must be excluded |
| `MONOREPO_MEMBERS` | a JSON array of `{name, path, capabilities}`, one per member. This drives per-member CI jobs, so a wrong path produces a job that tests nothing |
| `DEV_COMMAND` | only if the project actually serves something; empty drops the worktree dev-server block |
| `ADRS` | a JSON array of decisions, each needing `title`, `decision`, `rationale`, `consequences`. One file is written per entry. An ADR without a decision and its rationale is refused |
| `A11Y_SURFACES_JSON` | `{name, baseURL, routes}` per surface. `[]` records axe scanning as a gap rather than pretending to scan |
| `LOCALES_JSON`, `I18N_PROJECT_DIR` | shipped locales, and where the Inlang project sits relative to the deployable |

MUST supply a JSON-bearing answer as a **string**, in a data file or a quoted `--set`. The CLI
keeps it verbatim because these are declared `str`; a bare YAML list would render as a Python
repr and land in the file as invalid JSON.

`FORGE_PLATFORM` is worth calling out: it is a single answer that swaps the entire CI surface.
Ask it once, early. Only `github` and `gitlab` are supported; any other forge is an explicit
gap, not something to improvise.

Read a composed value back to the user before applying. Never invent a value that looks
plausible; leave it unset and name the gap.

## Brownfield

MUST read committed configuration before asking anything. Run
`git rev-parse --is-inside-work-tree` and count tracked files: no repo or zero tracked files
is greenfield.

For an existing repo, `plan --json` shows what each layer would overwrite. For every file
that already exists and would be replaced, ask `KEEP | CHANGE | REMOVE` and name the
consequence. Copier overwrites by default — the plan is your only warning.

The `.d/` fragment layers are additive: a new fragment lands beside the existing ones and the
generators fold it in, preserving text outside the managed markers.

## Adding a layer later

Re-run with the extra layer selected. Applying is idempotent, so unchanged files stay
byte-identical and your hand-written code is untouched.

```
project-setup apply --data-file .project-setup-answers.yml --dest . \
  --set WANT_LANG_RUST=true --json
```

## Gates

ASK before the first file is written (the plan gate).
ASK the licence, a published repository, a credential, or a machine-global registration.
MUST NOT write a secret-shaped value into a file. Say the value is now in a transcript and
must be rotated, and leave the setting a gap.

## Rules

MUST run `validate` before `plan`, and `plan` before `apply`. Each is cheap and writes nothing.
MUST report each command's own output. "Setup complete" in place of output hides a failure.
NOT copying template files by hand. `apply` places them byte-exactly; you cannot.
NOT editing a generated file. `.gitignore`, `.pre-commit-config.yaml` and the justfile import
  block are rewritten from `.d/` fragments; edit the fragment and re-run the generator.
NOT re-resolving pinned tool versions. They are pinned in the layers and Renovate bumps them.
NOT `--no-tasks` unless the user asks. Tasks are what initialise git, materialise the
  licence, and let the language's own tool own its manifest.
