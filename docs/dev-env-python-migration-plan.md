**Goal**
Replace the legacy shell dev-env implementation with a Python-native implementation. The shell script is not an API and should not shape the Python architecture. Preserve the user-facing product entrypoint, `./dportsv3 dev-env ...`, but not old internals, old state files, old cache layout, or old shell-created env compatibility.

**Non-Goals**
- No 1:1 function port.
- No compatibility with `state.env`.
- No compatibility with old shell-created envs.
- No separate raw extracted base cache unless proven necessary.
- No cache artifact push/pull yet.
- No support for non-chroot backends yet beyond keeping the model open for future jail support.

**Target Commands**
- `create`
- `shell`
- `destroy`
- `list`
- `cleanup-mounts`

**Target Layout**
```text
.cache/dports-dev/
  bases/
    archives/
      DragonFly-x86_64-....world.tar.gz
    provisioned/
      <provisioned-base-id>/
        root/
        metadata.json
        ready
  envs/
    <env-name>/
      env.json
      root/
      writable/
  repos/
    deltaports.git/
    freebsd-ports.git/
    dports.git/
  venvs/
    generator/
      <venv-id>/
        venv/
        metadata.json
        ready
  locks/
```

No `bases/extracted/`. Provisioning should be:

```text
archive -> temporary provisioned root -> package/tool bootstrap -> ready provisioned root
```

**Python Architecture**
- `cli.py`: `argparse` command dispatch only.
- `config.py`: typed env-var parsing and validation.
- `errors.py`: custom exceptions.
- `logging.py` or `log.py`: `INFO/WARN/ERROR`, step timing.
- `state.py`: `EnvironmentState` dataclass and JSON serialization.
- `store.py`: environment listing, lookup, creation, deletion.
- `locks.py`: lock-dir context manager.
- `mounts.py`: mount table parsing, null/procfs mount operations, ordered unmounting.
- `chroot.py`: controlled chroot command runner.
- `base.py`: Avalon archive discovery/download and provisioned base identity.
- `provision.py`: provisioned base creation and validation.
- `repos.py`: mirror refresh, clone/export operations.
- `runtime.py`: env root mount, writable overlays, `/dev`, `procfs`, distfiles, resolv.conf.
- `helpers.py`: generated helper scripts and shell rc templates.
- `dsynth.py`: dsynth config generation.
- `builder.py`: high-level `create` orchestration.
- `session.py`: `shell` orchestration.
- `commands/`: thin command handlers if `cli.py` grows too large.

**Core Domain Objects**
- `DevEnvConfig`
- `CreateOptions`
- `EnvironmentState`
- `EnvironmentStore`
- `BaseArchive`
- `ProvisionedBase`
- `RepoCache`
- `ChrootRunner`
- `MountManager`
- `EnvironmentBuilder`
- `EnvironmentSession`

**Error Model**
Use exceptions internally:
- `DevEnvError`
- `UsageError`
- `ConfigError`
- `StateError`
- `MountError`
- `ProvisionError`
- `CommandError`

The CLI catches known exceptions and prints:

```text
ERROR: message
```

Unexpected exceptions should not be over-masked during development.

**State Format**
Use `env.json`, not `state.env`.

Example shape:
```json
{
  "schema": 1,
  "name": "2026Q2-editors_vim",
  "backend": "chroot",
  "target": "@2026Q2",
  "origin": "editors/vim",
  "status": "ready",
  "created_at": "2026-05-05T12:00:00Z",
  "updated_at": "2026-05-05T12:10:00Z",
  "root_dir": "/root/.cache/dports-dev/envs/2026Q2-editors_vim/root",
  "writable_dir": "/root/.cache/dports-dev/envs/2026Q2-editors_vim/writable",
  "provisioned_base_id": "...",
  "repos": {
    "deltaports_branch": "master",
    "freebsd_branch": "2026Q2",
    "dports_branch": "staged"
  },
  "source": {
    "delta_root": "/path/to/DeltaPorts"
  },
  "runtime": {
    "host_distdir": "/usr/distfiles",
    "oracle_profile": "off"
  },
  "failure": null
}
```

Statuses:
- `creating`
- `ready`
- `failed`
- `destroying`

Failed envs are intentionally retained for investigation and removable with `destroy`.

**Locking**
Use lock directories under:

```text
.cache/dports-dev/locks/
```

Lock names:
- `archive-<asset>`
- `provision-<provisioned-base-id>`
- `repo-deltaports`
- `repo-freebsd-ports`
- `repo-dports`
- `venv-generator-<venv-id>`
- `env-<env-name>`

Implement `CacheLock` as a context manager:
- waits with timeout
- logs once when waiting
- removes lock on normal/exception exit
- refuses stale-lock deletion automatically unless we deliberately add a separate repair command later.

**Base Archive And Provisioning**
1. Fetch Avalon listing.
2. Select latest `DragonFly-x86_64-*.world.tar.gz`.
3. Download to `bases/archives/`.
4. Compute archive hash or include asset name plus file hash.
5. Compute `provisioned_base_id` from structured data:
   - archive identity
   - required package list
   - required command list
   - python package candidates
   - optional package list
   - helper script/template signature
   - provisioner schema version
6. If ready provisioned base exists and validates, reuse it.
7. Otherwise:
   - create temp dir under `bases/provisioned/`
   - extract archive directly into `tmp/root`
   - mount runtime needs for provisioning
   - bootstrap `pkg`
   - install required packages
   - install/find Python
   - install optional packages with warnings on failure
   - install helper scripts
   - prepare mountpoints
   - clean package caches
   - unmount runtime mounts
   - validate required tools
   - write `metadata.json`
   - write `ready`
   - atomic rename to final provisioned path.

**Runtime Mount Model**
Each env root is mounted from a provisioned base read-only.

Writable null mounts:
- `/work`
- `/root`
- `/tmp`
- `/var/tmp`
- `/etc/dsynth`
- `/construction`

Runtime mounts:
- `/dev`
- `procfs` at `/proc`
- host distfiles at `/usr/distfiles` if configured and present.

All mount operations should be idempotent and checked against the mount table.

Unmounting should always be deepest-first.

**Repo Cache**
Use `repos/` for mirrors:
- `deltaports.git`: mirror from selected local checkout.
- `freebsd-ports.git`: mirror from FreeBSD ports URL.
- `dports.git`: mirror from DPorts URL.

Create should:
- refuse dirty local DeltaPorts checkout unless `--allow-dirty`.
- refresh mirrors under locks.
- clone DeltaPorts branch into env-local `/work/DeltaPorts`.
- clone FreeBSD ports branch into env-local `/work/freebsd-ports`.
- export DPorts branch into env-local `/work/DPorts`.

No host source mount.

**Generator Venv Cache**
Cache generator venvs under:

```text
venvs/generator/<venv-id>/
```

`venv-id` derived from:
- provisioned base id
- chroot Python version
- generator `pyproject.toml` hash
- profile/schema version

Flow:
- restore cached venv into `/work/DeltaPorts/scripts/generator/.venv` if valid.
- validate with `/work/DeltaPorts/dportsv3 --help` inside chroot.
- rebuild if missing/invalid.
- store validated venv in cache.

**Create Flow**
Use `EnvironmentBuilder`, not shell-style function chaining.

High-level flow:
1. Parse options and validate.
2. Derive `freebsd_branch` from target if not provided.
3. Derive env name if not provided.
4. Acquire env lock.
5. Refuse existing env dir.
6. Create env dir and write `env.json` with `status=creating`.
7. Resolve/download archive.
8. Prepare provisioned base.
9. Refresh repo mirrors.
10. Mount provisioned base and writable overlays.
11. Mount runtime `/dev`, `/proc`, distfiles.
12. Seed env-local source trees.
13. Write dsynth config.
14. Write shell rc.
15. Prepare generator venv.
16. Mark state `ready`.
17. Run initial compose unless `--no-initial-compose`.
18. Record initial compose status separately from environment readiness.
19. If infrastructure failures occur:
   - mark state `failed`
   - record failure reason
   - retain env
   - return non-zero
20. If initial compose fails:
   - keep state `ready`
   - record `initial_compose.status=failed`
   - return non-zero
   - allow `--shell` because the environment is usable.

**Shell Flow**
Use `EnvironmentSession`.

Flow:
1. Load `env.json`.
2. Require backend `chroot`.
3. Ensure root is mounted from provisioned base.
4. Ensure writable overlays are mounted.
5. Refresh dsynth config and rcfile if `--refresh` or missing.
6. Ensure `/etc/resolv.conf`.
7. Mount runtime `/dev`, `/proc`, distfiles.
8. Exec `bash --noprofile --rcfile /root/.dports-dev-env.sh -i` if available.
9. Otherwise exec `/bin/sh`.

**Destroy Flow**
1. Load env record if present.
2. If env dir exists without state, treat as partial new-format env.
3. Set status `destroying` when possible.
4. Unmount everything under env dir deepest-first.
5. Refuse removal if any mounts remain.
6. Clear immutable flags if supported.
7. Remove env dir.
8. Do not remove shared caches.

**List Flow**
Output current tab-delimited summary:
```text
name	backend	target	origin	mount-status	status
```

Status comes from `env.json`; partial dirs are shown as `partial`.

**Cleanup-Mounts Flow**
- Root-only.
- Unmount everything under cache root deepest-first.
- If survivors remain, print them and fail.
- Do not delete files.

**Helper Scripts**
Generate from Python templates/resources:
- `regen`
- `reapply`
- `showenv`
- `dbuild`

They do not need byte-for-byte parity with shell output, only the intended functionality.

**CLI Options**
Keep user-facing options:
- `create --name`
- `create --target`
- `create --origin`
- `create --delta-root`
- `create --backend`
- `create --freebsd-branch`
- `create --dports-branch`
- `create --shell`
- `create --allow-dirty`
- `create --no-initial-compose`
- `create --oracle-profile`
- `shell NAME`
- `shell --refresh NAME`
- `destroy NAME`
- `list`
- `cleanup-mounts`

Use `argparse` subcommands.

**Delegation Removal**
After Python `create` exists:
1. Remove `legacy_script_path()`.
2. Remove `exec_legacy()`.
3. Unknown commands fail in Python.
4. Remove “legacy shell” text from help.
5. Delete `scripts/tools/dports-dev-env` outright.

**Suggested Commit Split**
1. `tools/dports-dev: introduce python dev-env architecture`
- exceptions, config, argparse, state store, layout constants.
2. `tools/dports-dev: implement python env state and mount lifecycle`
- JSON state, list, destroy, cleanup-mounts, mount manager.
3. `tools/dports-dev: implement python shell sessions`
- session, runtime mounts, dsynth/rc/helper generation.
4. `tools/dports-dev: implement python base provisioning`
- archive download, direct provisioned base build, validation.
5. `tools/dports-dev: implement python repo and venv caches`
- mirrors, source seeding, generator venv.
6. `tools/dports-dev: implement python create`
- builder orchestration and failure-retention.
7. `tools/dports-dev: remove legacy dev-env shell delegation`
- remove delegation and legacy script dependency.

**Verification**
Host-only:
- `python3 -m py_compile scripts/tools/dev-env/dports_dev_env/*.py`
- unit tests for pure modules
- `./dportsv3 dev-env --help`
- `./dportsv3 dev-env create --help`
- `./dportsv3 dev-env shell --help`
- `git diff --check`

DragonFly/root:
- `sudo ./dportsv3 dev-env list`
- `sudo ./dportsv3 dev-env cleanup-mounts --yes`
- `sudo ./dportsv3 dev-env create --target @2026Q2 --no-initial-compose`
- `sudo ./dportsv3 dev-env shell --refresh NAME`
- `sudo ./dportsv3 dev-env destroy NAME`
- reboot, then `sudo ./dportsv3 dev-env shell NAME`
- full create with compose enabled

**Decision**
Delete `scripts/tools/dports-dev-env` outright once delegation is gone. The Python implementation is authoritative; no shell compatibility wrapper is retained.
