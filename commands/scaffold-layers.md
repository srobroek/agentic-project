---
description: Scaffold a repository from layered Copier templates, or add a layer to an existing one.
---

Load `skill://scaffold-layers` and follow it.

Before anything else, decide whether you are needed at all. If this is a greenfield
repository and a preset fits, give the user the single command and stop:

```
project-setup presets
project-setup apply --preset <name> --dest . \
  --set PROJECT_NAME=<name> --set DESCRIPTION="<one line>" \
  --set CODEOWNER=@<owner> --set SECURITY_CONTACT=<contact>
```

Involve yourself only for: a brownfield repository, an answer that must be composed rather
than chosen, or the work that follows the scaffold.

$ARGUMENTS
