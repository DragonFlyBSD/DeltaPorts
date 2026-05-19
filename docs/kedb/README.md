# Known Error Database (KEDB)

This directory contains markdown files documenting known build issues specific to DragonFlyBSD DPorts. These files are automatically included in the triage agent's context to improve diagnosis accuracy.

## How It Works

The `agent-queue-runner` reads all `*.md` files from this directory (except `README.md` and `TEMPLATE.md`) and appends them to the triage payload sent to the `dports-triage` agent. This gives the agent knowledge of known patterns and proven fixes.

## Adding a New Entry

1. Copy `TEMPLATE.md` to a new file named after the issue category (e.g., `pthread.md`, `procfs.md`)
2. Fill in the sections with specific patterns, causes, and fixes
3. The runner will automatically pick it up on the next job

## Guidelines

1. **Be specific**: Include exact error patterns that can be matched in logs.
2. **Explain the cause**: Help the agent understand *why* this happens on DragonFly.
3. **Provide actionable fixes**: Describe DeltaPorts-style patches (files to modify, flags to add).
4. **Include examples**: Reference actual ports that were fixed this way.
5. **Keep files focused**: One file per issue category.
