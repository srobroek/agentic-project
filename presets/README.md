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

Agent: interview first — ask what is being built, offer the presets that could fit plus a
manual path, and customise from there. A preset is where the conversation starts, not a
substitute for it. Then copy the chosen preset, add the four identity keys and whatever the
user changed, `validate --json`, show the `plan`, and `apply --json` on approval.
