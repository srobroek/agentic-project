---
description: Set up a repository from layered Copier templates, or add a layer to an existing one.
---

Load `skill://project-setup` and follow it.

Interview the user. Start by asking what they are building, then offer the presets that could
fit alongside a fully manual path, and customise from there. A preset is the starting point of
the conversation, not a replacement for it — you cannot know whether one fits until you have
asked.

Ask in rounds, never one question at a time, and ask only what the selected layers declare:

```
project-setup presets --json      # the shapes, and the answers each carries
project-setup catalog --json      # every layer, every question, types, choices, defaults
```

Then `validate`, show the `plan`, and `apply` only once the user approves it.

If the repository already has tracked files, read its committed configuration before asking
anything.

$ARGUMENTS
