---
name: project-setup
description: Scaffolds a repository from layered Copier templates. Use when setting up a new project, or adding a capability layer to an existing one.
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

The CLI finds the layers itself: it asks `omp plugin list --json` for the installed
`@srobroek/project-setup` plugin on every run. No flag is needed when the plugin is installed
through OMP.

If `catalog` fails with `no templates directory found`, the plugin is not installed or is
disabled. Say so and stop. Its error names every place the CLI looked.

MUST NOT guess the plugin path from the working directory. The working directory is the user's
target repository, not this plugin — installing from it would install their project as the CLI.

### Installing the CLI

Only if `project-setup` is not on PATH at all, find the plugin's path first:

```sh
PLUGIN=$(omp plugin list --json | python3 -c '
import json, sys
for e in json.load(sys.stdin).get("npm", []):
    if e.get("name") == "@srobroek/project-setup":
        print(e["path"]); break')
```

Then install it:

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
  `SPDX_ID` takes `NONE` for a repository published under no licence at all — an
  internal service, a work repo. Do not default an unpublished project to Apache-2.0.

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

Present the stacks that could plausibly fit, each with the layers it selects, plus the manual
path. Do not present all twelve if two are relevant.

```
project-setup presets             # stacks, and the parts they are built from
project-setup presets --show <s>  # what a stack resolves to, and which part set each answer
project-setup catalog --json      # every layer and every question it declares
```

**Presets compose.** A stack is only a composition of parts, so a shape with no stack of its
own is still reachable — pass `--preset` more than once, later winning:

```
project-setup apply --preset parts/policy --preset parts/forge-github \
  --preset parts/lang-rust --preset parts/lang-ts ...
```

MUST NOT tell the user a shape is unsupported because no stack is named for it. Compose the
parts, and say which you combined.

### Round 3 — customise

**Preset path.** Read the preset's answers back grouped by topic, and ask what to change.
These are defaults to confirm, not questions to ask one at a time. In the same round, ask for
the four identity answers no preset can carry — `PROJECT_NAME`, `DESCRIPTION`, `CODEOWNER`,
`SECURITY_CONTACT` — and for whatever the preset documents as a gap.

**Manual path.** Show the layer catalog, always-on and opt-in separately, and take the
selection. Then ask only the questions those layers declare, grouped by layer, each with its
default and its permitted values.

**Both paths.** Two gates, two questions, asked once each. Whether the user wants to set
specific tool versions or take the pinned set — name the versions the selected layers would
use, so the answer is informed. And whether they want to change the shipped hook, CI and
README defaults. Expect no to both, and move on. That is two questions, not twenty-three.

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

MUST read which class a question is in before deciding whether to ask it. `catalog --json`
marks four, and they call for four different things:

| Marked | Do |
| --- | --- |
| `"derived": true` | never ask, never pass through. `derived_from` is an expression, not a value |
| `"composed": true` | never ask. Assemble it from the conversation and pass it with `--set` |
| `"pinned": true` | one question for the whole set, `PIN_TOOL_VERSIONS`. Default to the pins |
| `"tuned": true` | one question for the whole set, `CUSTOMISE_DEFAULTS`. Default to the shipped values |

MUST ask about tool versions exactly once, with one question: does the user want to set
specific versions, or take the pinned set? Every question marked `"pinned": true` — nineteen
toolchain and tool versions — is behind that one gate, `PIN_TOOL_VERSIONS`. Take the pins
unless the user asks otherwise: they are a tested combination and Renovate bumps them. If the
user does want to choose, ask only the pins the selected layers own, and only those. A user
who names one version in passing needs no gate: pass it with `--set` and say you did.

MUST ask about the shipped defaults the same way, once. Everything marked `"tuned": true` — a
hook size limit, a per-job CI timeout, the README's install and usage lines, the allowed
commit scopes — is behind `CUSTOMISE_DEFAULTS`. Reading seven thresholds out to somebody who
wanted a repository is the mistake this replaced. Take the defaults unless the user raises
one, and pass a value they do name with `--set`.

MUST NOT ask the user to decide something a task already decides. Native toolchain init is
not a question: `cargo init`, `bun init`, `uv init` and `go mod init` each skip when their
manifest already exists and warn when the tool is absent, so the right thing happens per
language without anybody choosing. Reconciling what those tools leave behind is the task's
job too, not yours: bun's generic CLAUDE.md and stale lockfile, uv's second interpreter pin,
the placeholder each one writes.

### Two answers are required; everything else has a value

Only `PROJECT_NAME` and `DESCRIPTION` have no default. Everything a user cannot reasonably
know at setup time — a production URL, an owner, a security contact — carries an obvious
placeholder instead of blocking the scaffold. `apply` lists them at the end.

MUST offer to replace a placeholder, and MUST NOT refuse to scaffold because one is unset. A
placeholder is a normal state for a new repository.

MUST leave the declared placeholder in place when the user does not know the value. Do not
substitute a stand-in of your own: `validate` recognises the declared string and nothing else,
so writing `@owner` over `@TODO-owner` reports a clean answer set and ships a CODEOWNERS file
naming nobody. `catalog --json` gives the exact placeholder per question.

MUST run `validate --json` to discover what is missing rather than reasoning about the
catalog's `required` flags. It reports `MISSING_REQUIRED`, `INVALID_CHOICE`, `INVALID_VALUE`,
`UNKNOWN_KEY`, `PLACEHOLDER_IN_USE`, `ANSWER_HAS_NO_EFFECT`, `ANSWER_CONTRADICTS_REPO`,
`STALE_FORGE_SURFACE` and `MEMBER_PATH_EMPTY` for the layers actually selected, which is the only thing that matters.
`INVALID_VALUE` is a declared validator rejecting the value — `PROJECT_NAME` reaches a crate,
module and package name, so it is the one most worth getting right before anything is written.

## Building the data file

1. **Start from the chosen preset**, or from an empty file on the manual path. Copy it; do not
   restate it.
2. **Add the two identity answers** `PROJECT_NAME` and `DESCRIPTION`, and `CODEOWNER` and
   `SECURITY_CONTACT` when the user knows them.
3. **Set the layer selection**: `WANT_<LAYER>: true`. `catalog --json` lists the exact names.
4. **Apply the customisations** the user asked for in round three.
5. **Validate before writing anything.**

```
project-setup validate --data-file answers.yml --json
```

The response is structured. `MISSING_REQUIRED` names the question and the layers that need
it. `INVALID_CHOICE` lists the permitted values. `INVALID_VALUE` quotes the pattern the answer
has to match. `UNKNOWN_KEY` means a typo or an unselected layer. Fix and re-validate; it
writes nothing.

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
| `INSTALL_COMMANDS`, `USAGE_EXAMPLE` | derived from the accepted stack. Behind `CUSTOMISE_DEFAULTS`, so pass a composed value with `--set` rather than opening the gate |
| `COMMIT_SCOPES` | the project's real module names. Behind the same gate |
| `HOOK_EXCLUDE_PATTERNS` | paths that genuinely must be excluded. Behind the same gate |
| `MONOREPO_MEMBERS` | a JSON array of `{name, path, capabilities}`, one per member. This drives per-member CI jobs, so a wrong path produces a job that tests nothing. `IS_MONOREPO` is derived from it: supply the members and do not set the flag. `validate` reports `MEMBER_PATH_EMPTY` for a member path with no manifest yet. Apply writes the language starters at the root, so tell the user their code has to move into the member paths |
| `DEV_COMMAND` | only if the project actually serves something; empty drops the worktree dev-server block |
| `ADRS` | a JSON array of decisions, each needing `title`, `decision`, `rationale`, `consequences`. One file is written per entry. An ADR without a decision and its rationale is refused |
| `A11Y_SURFACES_JSON` | `{name, baseURL, routes}` per surface. `[]` records axe scanning as a gap rather than pretending to scan |
| `LOCALES_JSON`, `I18N_PROJECT_DIR` | shipped locales, and where the Inlang project sits relative to the deployable. The layer writes only the base locale's catalog, so `just i18n` fails for each other locale until its catalog exists, and names the file to write |

MUST supply a JSON-bearing answer as a **string**, in a data file or a quoted `--set`. The CLI
keeps it verbatim because these are declared `str`; a bare YAML list would render as a Python
repr and land in the file as invalid JSON.

`FORGE_PLATFORM` is worth calling out: it is a single answer that swaps the entire CI surface.
The interview asks it third, straight after the layer selection, and you should too. Only
`github` and `gitlab` are supported; any other forge is an explicit gap, not something to
improvise. Changing it later excludes the other forge's files rather than deleting them, so
`validate` reports `STALE_FORGE_SURFACE` and the files have to go by hand.

Read a composed value back to the user before applying. Never invent a value that looks
plausible; leave it unset and name the gap.

## Brownfield

MUST read committed configuration before asking anything. Run
`git rev-parse --is-inside-work-tree` and count tracked files: no repo or zero tracked files
is greenfield.

For an existing repo, `plan` rehearses the whole apply in a copy and reports what changed, so
the lists are what `apply` will do, not a prediction. In `--json`, `files` has four lists:
`create`, `overwrite`, `merge` and `remove`. `overwrite` is an existing file that loses at
least one non-blank line, and `lines_lost` counts them per file. `merge` keeps every line.
`links` names a file that becomes a symlink: a hand-written `CLAUDE.md` is folded into
`AGENTS.md` and linked to it, so it is on `merge`.

MUST read every list rather than guessing from the layer set. For every `overwrite` entry, ask
`KEEP | CHANGE | REMOVE` and name the lines lost. Copier overwrites by default, and the plan
is your only warning. `remove` should be empty; report any entry on it.

A file a step leaves alone is on no list. A hand-written `.github/workflows/ci.yml` is one:
the plan's warnings then say the `wc-*` workflows beside it are called by nothing. Report
that line, because it is a real gap the user has to close by hand or by deleting the file.
`ok: false` in a plan means the apply would stop at that step. Report the step and do not
apply.

`INTERRUPTED_APPLY` means an earlier apply did not finish. Re-running the same apply
finishes it.

MUST report any step that `apply` says "did less than the full job". That list is how a
missing toolchain surfaces: `cargo init` is skipped rather than failing the scaffold, so the
repository has no `Cargo.toml` and the run still exits 0.

The `.d/` fragment layers are additive: a new fragment lands beside the existing ones and the
generators fold it in, preserving text outside the managed markers.

## Adding or dropping a layer later

Re-run with the extra layer selected. Applying is idempotent, so unchanged files stay
byte-identical and your hand-written code is untouched.

```
project-setup apply --data-file .project-setup-answers.yml --dest . \
  --set WANT_LANG_RUST=true --json
```

Reuse the recorded answers and add only the selection. `WANT_<LAYER>` is the key to write; the
interview's `LAYERS` multiselect is another spelling of the same thing and both are read.

Dropping a layer is not symmetrical. Deselecting it stops it being written; nothing deletes
what an earlier apply wrote. `validate` reports `STALE_LAYER_FILES`, naming the files that
layer left behind, and removing them is the user's decision to confirm — not yours to take.

Changing an answer that already fed a generated file re-derives it. `COMMIT_SCOPES` is folded
into `.pre-commit-config.yaml`; a new value replaces that hook and `merge_hooks.py` reports
which entry it replaced. Anything no fragment declares is left alone, so a repository's own
hooks survive. Report the replacement line — it is the confirmation the change landed.

## Rules

MUST NOT write a secret-shaped value into a file. Say the value now sits in a transcript and
  has to be rotated, and leave that setting a gap.
MUST run `validate` before `plan`, and `plan` before `apply`. Each is cheap and writes nothing.
MUST report each command's own output. "Setup complete" in place of output hides a failure.
NOT copying template files by hand. `apply` places them byte-exactly; you cannot.
NOT editing a generated file. `.gitignore`, `.pre-commit-config.yaml` and the justfile import
  block are rewritten from `.d/` fragments; edit the fragment and re-run the generator.
NOT looking up the latest release of a pinned tool. The pins are a tested set and Renovate
  bumps them; ask whether the user wants to choose, and take their answer if they do.
NOT `--no-tasks` unless the user asks. Tasks are what initialise git, materialise the
  licence, and let the language's own tool own its manifest.
