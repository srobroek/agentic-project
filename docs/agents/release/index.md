# Release

<!-- BEGIN GENERATED: release-index -->
Tool: release-please. Tags: `v<version>`.

A version is never tagged by hand. The release tool derives the next one from the
Conventional Commit subjects in the range, so a hand-made tag makes it compute the
wrong version, and the quality workflow refuses one.
<!-- END GENERATED: release-index -->

## Committed inputs

`renovate.json` records dependency-update policy. Enabling Renovate and supplying its
credentials are forge or operator state, not repository-file behavior.

`release-please-config.json` records release-please policy. The project-setup release workflow
runs on GitHub only and uses the repository's default branch after the plan resolves it. A
GitLab release process is manual unless a separate runner is supplied and recorded.

For a monorepo, release package paths and component boundaries come from the accepted member
map. For a polyrepo, ask before reusing a related repository's release choices.

## Notes

Hand-written. Text outside the markers survives regeneration.
