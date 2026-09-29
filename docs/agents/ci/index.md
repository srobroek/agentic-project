# CI

<!-- BEGIN GENERATED: ci-index -->
Reusable workflows, wired by `ci.yml`, which `just ci-sync` generates:

```
ci.yml
release-please.yml
wc-changes.yml
wc-gate.yml
wc-lint-python.yml
wc-quality.yml
wc-security.yml
wc-test-python.yml
```

`wc-gate.yml` is the only required status check. It lists every other job in
`needs:` and receives `toJSON(needs)`, so a new job is covered without touching
branch protection.

Reproduce a failure locally with `just check`. The hook set runs there and in CI
through `prek run --all-files`, so a local pass means the same thing.
<!-- END GENERATED: ci-index -->

## Supported forge paths

GitHub uses the generated caller under `.github/workflows/`. GitLab uses the checked-in
`.gitlab-ci.yml` root include and `.gitlab/ci/*.yml` fragments.

Gitea, Azure DevOps, and other forges are explicit unsupported gaps. Do not treat a copied
GitHub workflow or GitLab pipeline as support for those forges.

## Member paths

A monorepo's CI jobs use the accepted member paths. A polyrepo runs its own checkout's jobs;
ask before reusing a related repository's CI or release choices.

## Notes

Hand-written text outside generated markers survives regeneration.
