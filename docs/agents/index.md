# Steering index

One directory per concern. Read the index, then the leaf for the language being touched.

| Concern | Directory |
|---|---|
| What each quality tool enforces | `quality/` |
| Job graph and merge gates | `ci/` |
| Version source and publish targets | `release/` |
| Test layout and invocation | `testing/` |
| Where docs live and how they deploy | `docs/` |
| Required environment variables | `env/` |
| Error handling, naming, ownership | `conventions.md` |

<!-- BEGIN GENERATED: index -->
## Commands

Every task runs through `just`. The surface:

```
apply audit audit-list catalog check ci-sync
e2e hooks-all hooks-group hooks-install hooks-merge omp-link
omp-verify plan port python python-cov python-deps
python-docs python-fmt python-fmt-check python-install python-lint python-matrix
python-test python-types setup steering steering-check test
```

## Toolchain

Pinned in `.mise/conf.d/`, so CI and a laptop resolve the same versions.

| Tool | Version |
|---|---|
| `actionlint` | `1.7.12` |
| `aqua:suzuki-shunsuke/pinact` | `5.0.0` |
| `betterleaks` | `1.8.1` |
| `just` | `1.58.0` |
| `lychee` | `0.24.2` |
| `prek` | `0.4.11` |
| `python` | `3.14` |
| `shellcheck` | `0.11.0` |
| `taplo` | `0.10.0` |
| `trufflehog` | `3.97.9` |
| `typos` | `1.50.2` |
| `ubi:opengrep/opengrep` | `1.26.0` |
| `uv` | `0.12.18` |
| `zizmor` | `1.28.0` |

## Languages

`python`.

Each has a quality leaf under `quality/`.
<!-- END GENERATED: index -->

## Using the recipes

For a monorepo, use the accepted member paths and the recipe already provided by that member's
language asset. Creating a member by hand does not register it anywhere; update the accepted
member map and add the member's bounded fragments explicitly.

For a polyrepo, run the setup independently in each checkout. Ask before reusing a related
repository's topology, host, forge, stack, release, or governance choices.

Run `just steering` after changing configuration that a generated block reads. CI fails on
steering drift.

Run the relevant root or member recipe before claiming a change works. A tree that renders is
not a project that builds.
