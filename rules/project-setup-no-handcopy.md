---
name: project-setup-no-handcopy
description: When scaffolding a repository or adding project tooling that a scaffold layer owns.
---

# Do not hand-copy scaffold assets

`project-setup apply` places template files byte-exactly and records what it wrote. Copying
those files yourself reintroduces the drift the tool removes, and it cannot be verified.

MUST use `project-setup apply` to place any file a layer owns. Run
`project-setup catalog --json` to see which layers own what.

MUST edit the fragment, not the generated file. `.gitignore`, `.pre-commit-config.yaml`, the
justfile import block, `.github/workflows/ci.yml` and `docs/agents/` are rewritten from
`.d/` fragments and from the tree. A hand edit to a generated file is lost on the next run.

MUST validate before applying. `project-setup validate --json` reports missing required
answers, invalid choices and unknown keys, and writes nothing.

NOT resolving tool versions at scaffold time. They are pinned in the layers and Renovate
owns the bumps.
