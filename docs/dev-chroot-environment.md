# Dev Chroot Environment

`scripts/tools/dports-dev-env` creates a throwaway DragonFly chroot for port
development while reusing cached inputs.

Current scope:

- backend: `chroot` only
- rootfs source: latest `DragonFly-x86_64-*.world.tar.gz` from Avalon releases
- DeltaPorts checkout: mounted live from the host
- FreeBSD ports source: persistent mirror plus branch worktree

## Requirements

- run as `root`
- host commands: `curl`, `tar`, `git`, `chroot`, `mount_null`, `mount_procfs`
- network access to Avalon and the FreeBSD ports git remote

## Create One Environment

```bash
sudo scripts/tools/dports-dev-env create --target @2026Q2 --origin editors/vim --shell
```

This will:

1. discover the latest DragonFly `*world*` asset on Avalon,
2. cache the downloaded archive,
3. extract it once into the shared base cache,
4. create a throwaway env root from that cache,
5. mount the host DeltaPorts checkout into `/work/DeltaPorts`,
6. create or refresh a cached FreeBSD ports worktree for the target branch,
7. mount that worktree into `/work/freebsd-ports`,
8. attempt to bootstrap a few development tools inside the chroot,
9. run `compose` with `--oracle-profile off`,
10. drop you into a shell if `--shell` was requested.

## Enter Later

```bash
sudo scripts/tools/dports-dev-env shell 2026Q2-editors_vim
```

## Destroy

```bash
sudo scripts/tools/dports-dev-env destroy 2026Q2-editors_vim
```

## List

```bash
scripts/tools/dports-dev-env list
```

## Default Layout

By default the helper stores state under `~/.cache/dports-dev/`:

- `base-downloads/`: downloaded Avalon archives
- `base-extracted/`: extracted reusable clean rootfs trees
- `repos/freebsd-ports.git/`: persistent git mirror
- `worktrees/freebsd-ports/<branch>/`: cached worktrees
- `envs/<name>/root/`: throwaway chroot root

## In-Chroot Helpers

The shell defines:

- `regen`: rerun compose for the environment target
- `reapply`: rerun `dsl apply` for the selected origin
- `showenv`: print `DPORTS_*` environment variables

## Notes

- `compose` failures do not delete the environment; the root is kept for manual
  inspection.
- The helper preserves DragonFly immutable flags during extraction and env
  creation, and only clears them during cleanup so environments can still be
  removed reliably.
- Tool bootstrapping is best-effort beyond the required package list. The exact
  package name providing `genpatch` may need adjustment for your host package
  repositories.
- The helper is intentionally structured around a `BACKEND` field in state so a
  future jail backend can reuse the same UX.
