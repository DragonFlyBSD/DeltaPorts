# Dev Chroot Environment

`./dportsv3 dev-env` creates a throwaway DragonFly chroot for port
development while reusing cached inputs.

Current scope:

- backend: `chroot` only
- rootfs source: latest `DragonFly-x86_64-*.world.tar.gz` from Avalon releases
- DeltaPorts source: cached mirror plus env-local writable `master` checkout
- FreeBSD ports source: persistent mirror plus env-local git checkout for the
  target branch
- DPorts source: persistent mirror, exported into the env and used as
  `--lock-root`
- Host distfiles: mounted into the env at `/usr/distfiles` by default so
  repeated runs can share fetched distfiles
- dsynth: generated config in the env, using the composed tree as its ports
  directory and `/work/dsynth/` for build outputs

## Requirements

- run as `root`
- host commands: `tar`, `git`, `chroot`, `mount_null`, `mount_procfs`
- network access to Avalon, the FreeBSD ports git remote, and the DPorts git
  remote

## Create One Environment

```bash
sudo ./dportsv3 dev-env create --target @2026Q2 --origin editors/vim --shell
```

This will:

1. discover the latest DragonFly `*world*` asset on Avalon,
2. cache the downloaded archive,
3. build or reuse a provisioned base directly from the cached archive,
4. refresh cached mirrors for DeltaPorts, FreeBSD ports, and DPorts,
5. mount the provisioned base read-only and add per-env writable overlays,
6. clone env-local DeltaPorts and FreeBSD ports from cached mirrors and export
   the DPorts tree into the env,
7. run `cd /usr && make pkg-bootstrap` when `pkg` is missing, then bootstrap a
   few development tools inside the chroot,
8. generate `/etc/dsynth/dsynth.ini` and `/etc/dsynth/DPortsDev-make.conf`,
9. run `compose` with `--oracle-profile off` and `--lock-root /work/DPorts`,
10. drop you into a shell if `--shell` was requested.

## Enter Later

```bash
sudo ./dportsv3 dev-env shell 2026Q2-editors_vim
```

## Destroy

```bash
sudo ./dportsv3 dev-env destroy 2026Q2-editors_vim
```

## List

```bash
sudo ./dportsv3 dev-env list
```

`list` also shows partial or still-creating environments left by interrupted
creation so they can be destroyed explicitly.

## Cleanup Mounts

```bash
sudo ./dportsv3 dev-env cleanup-mounts --yes
```

This previews stale dports-dev mounts under the cache root and requires `--yes`
before unmounting them. If a mount target path was already removed by an
interrupted run, DragonFly may require a reboot to clear the orphaned mount.

## Default Layout

By default the helper stores state under `~/.cache/dports-dev/`:

- `bases/archives/`: downloaded Avalon archives
- `bases/provisioned/`: reusable DragonFly roots with `pkg`, tools, Python, and
  optional helper packages already installed
- `repos/deltaports.git/`: cached DeltaPorts mirror
- `repos/freebsd-ports.git/`: persistent git mirror
- `repos/dports.git/`: persistent DPorts mirror
- `venvs/generator/`: DragonFly-native cached generator virtualenvs
- `envs/<name>/root/`: chroot mount point for the read-only provisioned base
- `envs/<name>/writable/`: per-env writable trees mounted over `/work`,
  `/root`, `/tmp`, `/var/tmp`, `/etc/dsynth`, and `/construction`

The provisioned base cache is keyed by the Avalon world asset and configured
tool package lists. Fresh envs mount this provisioned root read-only and layer
small writable per-env directories over the paths that need mutation, avoiding
both repeated `pkg-bootstrap` work and full root copies.

The generator virtualenv cache is keyed by provisioned base id, Python version,
and `scripts/generator/pyproject.toml`. It is restored into
fresh envs before the initial compose, avoiding repeated `pip install -e` work
while still letting the editable install use the env-local DeltaPorts sources.

Default shared distfiles mount:

- host: `/usr/distfiles`
- env: `/usr/distfiles`

Override with `DPORTS_DEV_HOST_DISTDIR` or set it to an empty value to disable
the mount.

## In-Chroot Helpers

The shell defines:

- `regen`: rerun compose for the environment target
- `reapply`: rerun `dsl apply` for the selected origin
- `dbuild`: run `dsynth -p DPortsDev build` for the selected origin, or for
  origins passed as arguments
- `showenv`: print `DPORTS_*` environment variables

The generated dsynth profile is `DPortsDev` and uses:

- ports tree: `/work/artifacts/compose/<target>`
- packages: `/work/dsynth/packages`
- repository: `/work/dsynth/packages/All`
- build root: `/work/dsynth/build`
- logs: `/work/dsynth/logs`
- options: `/work/dsynth/options`
- distfiles: `/usr/distfiles`

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
