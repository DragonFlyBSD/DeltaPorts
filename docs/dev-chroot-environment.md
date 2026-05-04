# Dev Chroot Environment

`scripts/tools/dports-dev-env` creates a throwaway DragonFly chroot for port
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
- host commands: `curl`, `tar`, `git`, `chroot`, `mount_null`, `mount_procfs`
- network access to Avalon, the FreeBSD ports git remote, and the DPorts git
  remote

## Create One Environment

```bash
sudo scripts/tools/dports-dev-env create --target @2026Q2 --origin editors/vim --shell
```

The repo wrapper also dispatches to the same helper:

```bash
sudo ./dportsv3 dev-env create --target @2026Q2 --origin editors/vim --shell
```

This will:

1. discover the latest DragonFly `*world*` asset on Avalon,
2. cache the downloaded archive,
3. extract it once into the shared base cache,
4. refresh cached mirrors for DeltaPorts, FreeBSD ports, and DPorts,
5. create the throwaway env root from the cached world,
6. clone env-local DeltaPorts and FreeBSD ports from cached mirrors and export
   the DPorts tree into the env,
7. run `cd /usr && make pkg-bootstrap` when `pkg` is missing, then bootstrap a
   few development tools inside the chroot,
8. generate `/etc/dsynth/dsynth.ini` and `/etc/dsynth/DPortsDev-make.conf`,
9. run `compose` with `--oracle-profile off` and `--lock-root /work/DPorts`,
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
- `repos/deltaports.git/`: cached DeltaPorts mirror
- `repos/freebsd-ports.git/`: persistent git mirror
- `repos/DPorts.git/`: persistent DPorts mirror
- `venvs/dportsv3/`: DragonFly-native cached `dportsv3` virtualenvs
- `envs/<name>/root/`: throwaway chroot root

The `dportsv3` virtualenv cache is keyed by DragonFly release, Python version,
install profile, and `scripts/generator/pyproject.toml`. It is restored into
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
