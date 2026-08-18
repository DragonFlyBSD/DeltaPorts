# Agent Instructions

The tooling that composes this overlay lives in a separate repository,
**Polytropos**, and so does its issue tracker. This is a data repository:
ports overlays and patches.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

## What this repository is

A **data** repository: the overlays and patches that turn the FreeBSD Ports
Collection into DragonFly Ports.

- `ports/<category>/<port>/` — per-port overlay (`STATUS`, `overlay.dops`,
  `dragonfly/`, `diffs/`, `newport/`)
- `special/` — non-port overlays (`Mk`, `Templates`)
- `scripts/` — the shell generator, plus Tinderbox/builder hooks

There is no build or test suite here. Changes are validated by composing and
building the affected ports.

## The tooling lives elsewhere

The Python toolchain — the compose pipeline, the `overlay.dops` DSL and
engine, the migration program, the build tracker, the chroot dev-env manager
and the agentic build-failure repair loop — is a separate project,
**Polytropos**. It reads this repository as an input via `--delta-root` /
`$DPORTS_DELTA_ROOT`.

Do not reintroduce it here. If a task needs a change to the DSL, the compose
pipeline, the tracker or the agent, that change belongs in Polytropos; what
belongs here is the overlay data those tools consume.
