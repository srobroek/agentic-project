---
name: project-setup
description: Scaffolds a repository from layered Copier templates. Use when setting up a new project, or adding a capability layer to an existing one. The templates own the questions; you only choose layers and supply answers.
---

# Project Setup

TRIGGER
+ the `/project-setup` command ran
+ "set up a project", "scaffold this repo", "add CI/hooks/a language layer"
+ a new repository with no tooling, or an existing one missing a layer
- changing one tool's config in a repo already set up → that tool's own skill
- authoring a new layer → `references/authoring.md`
- an existing repo already set up, one tool to change → that tool's own skill

## Preflight

The plugin ships the templates. The CLI is a separate Python package, so two things have to
be true before you can scaffold: the command exists, and it can find this plugin's layers.

Check both at once:

```sh
project-setup catalog >/dev/null 2>&1 && echo ready
```

If that prints `ready`, skip the rest of this section.

### Finding the layers

The templates belong to the plugin, not to the CLI, so the CLI has to be told where they are.
Ask OMP for the plugin's own path rather than guessing it:

```sh
PLUGIN=$(omp plugin list --json | python3 -c '
import json, sys
for e in json.load(sys.stdin).get("npm", []):
    if e.get("name", "").endswith("/project-setup"):
        print(e["path"]); break')
echo "$PLUGIN"
```

Then pass it on every invocation:

```sh
project-setup --templates "$PLUGIN/templates" --presets "$PLUGIN/presets" catalog
```

MUST pass the two flags on every call once you need them. Each of your shell commands runs in
a fresh process, so an `export` in one does not survive into the next. `PROJECT_SETUP_TEMPLATES`
and `PROJECT_SETUP_PRESETS` exist for a human's shell profile, not for you.

MUST NOT guess the plugin path from the working directory. The working directory is the user's
target repository, not this plugin — installing from it would install their project as the CLI.

### Installing the CLI

Only if `project-setup` is not on PATH at all:

```sh
uv tool install "$PLUGIN"          # a marketplace install: a copy, install it as-is
uv tool install --editable "$PLUGIN"   # a linked plugin: tracks the working tree
```

`--editable` only matters for plugin development, where you want code changes picked up
without reinstalling. For normal use the plain form is correct.

MUST NOT proceed by hand when the CLI is missing. Copying template files yourself is the
failure mode this plugin exists to remove. Say the CLI is unavailable and stop.

## Interview first, always

Setting up a repository is a conversation. Conduct it — the templates make it short, they do
not replace it. A preset is where the conversation *starts*, not a reason to skip it: you
cannot know whether a preset fits until you have asked what the user is building.

GATES
ASK the shape before reading any answer back
ASK the plan, before the first file is written
ASK the licence, a published repository, a credential, a machine-global registration

Two entry paths. Offer both in round two and let the user pick:

| Path | When |
| --- | --- |
| **Preset, then customise** | a listed shape is close. Most projects |
| **Manual** | nothing is close, or the user wants to see every layer |

### Round 1 — what are you building?

One open question, and the only genuinely open-ended one. Everything after it is bounded by
the catalog.

For a brownfield repository, read the committed configuration *before* asking anything; see
the Brownfield section.

### Round 2 — the shape

Present the presets that could plausibly fit, each with the layers it selects and the notable
choices it fixes, plus the manual path. Do not present all ten if two are relevant.

```
project-setup presets --json      # every shape and the answers it carries
project-setup catalog --json      # every layer and every question it declares
```

### Round 3 — customise

**Preset path.** Read the preset's answers back grouped by topic, and ask what to change.
These are defaults to confirm, not questions to ask one at a time. In the same round, ask for
the four identity answers no preset can carry — `PROJECT_NAME`, `DESCRIPTION`, `CODEOWNER`,
`SECURITY_CONTACT` — and for whatever the preset documents as a gap.

**Manual path.** Show the layer catalog, always-on and opt-in separately, and take the
selection. Then ask only the questions those layers declare, grouped by layer, each with its
default and its permitted values.

MUST ask in rounds. One question at a time turns a two-minute conversation into twenty.

MUST offer, once, that the scaffold runs without a model, so the user can re-run or reproduce
it themselves. It is a property worth knowing, not a reason for you to leave:

```
project-setup apply --preset <name> --dest . \
  --set PROJECT_NAME=<name> --set DESCRIPTION="<one line>" \
  --set CODEOWNER=@<owner> --set SECURITY_CONTACT=<contact>
```

### Round 4 — composed answers

Only if the selected layers need them. See "Composed answers" below.

### Round 5 — plan, then apply

`validate`, then `plan`, then show the plan and wait. Apply only on approval.

## What bounds the interview

The template layers are the question set. Read it, never invent it.

MUST NOT ask for a value that `catalog --json` does not list for a selected layer. If you
believe a question is missing, the answer is a **missing layer**, not a missing question — say
so rather than improvising a question.

MUST NOT re-ask anything the user has already told you, in this conversation or in a
`.project-setup-answers.yml` already in the repository. Read it back instead.

MUST NOT ask for a tool version. They are pinned in the layers and Renovate bumps them.

## Building the data file

1. **Start from the chosen preset**, or from an empty file on the manual path. Copy it; do not
   restate it.
2. **Add the four identity answers**: `PROJECT_NAME`, `DESCRIPTION`, `CODEOWNER`,
   `SECURITY_CONTACT`.
3. **Set the layer selection**: `WANT_<LAYER>: true`. `catalog --json` lists the exact names.
4. **Apply the customisations** the user asked for in round three.
5. **Validate before writing anything.**

```
project-setup validate --data-file answers.yml --json
```

The response is structured. `MISSING_REQUIRED` names the question and the layers that need
it. `INVALID_CHOICE` lists the permitted values. `UNKNOWN_KEY` means a typo or an unselected
layer. Fix and re-validate; it writes nothing.

6. **Show the plan and wait.** `plan` is a dry run and mutates nothing:

```
project-setup plan --data-file answers.yml --dest . --json
```

7. **Apply** only after the user approves the plan.

```
project-setup apply --data-file answers.yml --dest . --json
```

8. **Then the work that matters**: the first real code, the libraries, the layout. The
   scaffold is the floor, not the deliverable.

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

Reuse the recorded answers and add only the selection. Changing an answer that feeds an
already-merged file is refused rather than overwritten: `COMMIT_SCOPES` has been folded into
`.pre-commit-config.yaml`, so a new value reports a conflict. To change one, delete the
generated file and re-apply.

## Rules

MUST NOT write a secret-shaped value into a file. Say the value now sits in a transcript and
  has to be rotated, and leave that setting a gap.
MUST run `validate` before `plan`, and `plan` before `apply`. Each is cheap and writes nothing.
MUST report each command's own output. "Setup complete" in place of output hides a failure.
NOT copying template files by hand. `apply` places them byte-exactly; you cannot.
NOT editing a generated file. `.gitignore`, `.pre-commit-config.yaml` and the justfile import
  block are rewritten from `.d/` fragments; edit the fragment and re-run the generator.
NOT re-resolving pinned tool versions. They are pinned in the layers and Renovate bumps them.
NOT `--no-tasks` unless the user asks. Tasks are what initialise git, materialise the
  licence, and let the language's own tool own its manifest.
