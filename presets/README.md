# Presets

A preset is a **project shape**: which layers, which pinned versions, which policy
choices. It deliberately does *not* carry project identity.

Four answers are always per-project and never live in a preset:

| Answer | Why it cannot be preset |
| --- | --- |
| `PROJECT_NAME` | unique per repository |
| `DESCRIPTION` | unique per repository |
| `CODEOWNER` | team-specific |
| `SECURITY_CONTACT` | team-specific |

So `project-setup validate --preset ts-service` reports exactly four
`MISSING_REQUIRED` errors. That is the preset working as intended: it tells you
the shortest possible list of things only you can answer.

Human:

    project-setup apply --preset ts-service --dest ../my-app \
      --set PROJECT_NAME=my-app --set DESCRIPTION="Thing that does X" \
      --set CODEOWNER=@me --set SECURITY_CONTACT=security@example.com

Agent: copy the preset, add the four keys, write one file, `validate --json`, `apply --json`.
