---
description: Set up a repository from layered Copier templates, or add a layer to an existing one.
---

Load `skill://project-setup` and follow it.

Interview the user. Ask first what they are building. Then offer the presets that could fit,
alongside a fully manual path. Customize the answers from there.

A preset is where the conversation starts. You have to ask what the user is building before
you can tell whether one fits.

Ask in rounds. Ask only what the selected layers declare:

```
project-setup presets --json      # the shapes, and the answers each carries
project-setup catalog --json      # every layer, every question, types, choices, defaults
```

If the repository already has tracked files, read its committed configuration first.

Then run `validate`. Show the `plan`. Once the user approves it, run `apply`.

$ARGUMENTS
