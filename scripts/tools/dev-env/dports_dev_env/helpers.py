from __future__ import annotations

import hashlib
import shlex

from .dsynth import dsynth_profile_name
from .state import EnvironmentState


def quote(value: str) -> str:
    return shlex.quote(value)


HELPER_NAMES = ["regen", "reapply", "showenv", "dbuild", "dtest"]
TOUCHED_ORIGINS_PATH = "/work/.dports-dev-touched-origins"
HELPER_BIN_DIR = "/root/.dports-dev/bin"


def helper_body(name: str) -> str:
    if name == "regen":
        return """#!/bin/sh
set -eu
: "${DPORTS_TARGET:?regen: DPORTS_TARGET is not set; run from a dports-dev shell}"
: "${DPORTS_COMPOSE_ROOT:?regen: DPORTS_COMPOSE_ROOT is not set}"
: "${DPORTS_LOCK_ROOT:?regen: DPORTS_LOCK_ROOT is not set}"
: "${DPORTS_ORACLE_PROFILE:=off}"
if [ -n "${DPORTS_ORIGIN:-}" ]; then
    exec /work/DeltaPorts/dportsv3 compose --target "$DPORTS_TARGET" --delta-root /work/DeltaPorts --freebsd-root /work/freebsd-ports --lock-root "$DPORTS_LOCK_ROOT" --output "$DPORTS_COMPOSE_ROOT" --replace-output --oracle-profile "$DPORTS_ORACLE_PROFILE" --origin "$DPORTS_ORIGIN"
fi
exec /work/DeltaPorts/dportsv3 compose --target "$DPORTS_TARGET" --delta-root /work/DeltaPorts --freebsd-root /work/freebsd-ports --lock-root "$DPORTS_LOCK_ROOT" --output "$DPORTS_COMPOSE_ROOT" --replace-output --oracle-profile "$DPORTS_ORACLE_PROFILE"
"""
    if name == "reapply":
        return f"""#!/bin/sh
set -eu
: "${{DPORTS_TARGET:?reapply: DPORTS_TARGET is not set; run from a dports-dev shell}}"
: "${{DPORTS_COMPOSE_ROOT:?reapply: DPORTS_COMPOSE_ROOT is not set}}"
: "${{DPORTS_LOCK_ROOT:?reapply: DPORTS_LOCK_ROOT is not set}}"
: "${{DPORTS_ORACLE_PROFILE:=off}}"
origins_file="${{DPORTS_TOUCHED_ORIGINS_FILE:-{quote(str(TOUCHED_ORIGINS_PATH))}}}"
if [ "$#" -eq 0 ]; then
    if [ -s "$origins_file" ]; then
        set --
        while IFS= read -r origin; do
            [ -n "$origin" ] || continue
            set -- "$@" "$origin"
        done < "$origins_file"
    elif [ -n "${{DPORTS_ORIGIN:-}}" ]; then
        set -- "$DPORTS_ORIGIN"
    else
        printf '%s\n' 'usage: reapply ORIGIN... (or run sync-dirty first, or create the env with --origin)' >&2
        exit 1
    fi
fi
for origin in "$@"; do
    /work/DeltaPorts/dportsv3 compose --target "$DPORTS_TARGET" --origin "$origin" --delta-root /work/DeltaPorts --freebsd-root /work/freebsd-ports --lock-root "$DPORTS_LOCK_ROOT" --output "$DPORTS_COMPOSE_ROOT" --oracle-profile "$DPORTS_ORACLE_PROFILE" || exit $?
done
"""
    if name == "showenv":
        return """#!/bin/sh
env | grep '^DPORTS_' | sort
"""
    if name in ("dbuild", "dtest"):
        # dbuild runs `dsynth build` (build + stage + package, the
        # patch loop's fast iteration target). dtest runs
        # `dsynth test`, which exercises the extra Q/A phases
        # (stage-qa, check-plist, the test target) AND force-rebuilds
        # by removing any existing package first — the heavier gate
        # verify-fix uses before an operator accepts a fix.
        subcommand = "build" if name == "dbuild" else "test"
        return f"""#!/bin/sh
set -eu
: "${{DPORTS_TARGET:?{name}: DPORTS_TARGET is not set; run from a dports-dev shell}}"
: "${{DPORTS_DSYNTH_PROFILE:?{name}: DPORTS_DSYNTH_PROFILE is not set; run from a dports-dev shell}}"
if ! command -v dsynth >/dev/null 2>&1; then
    printf '%s\n' '{name} requires dsynth; recreate the env with dsynth available' >&2
    exit 1
fi
if [ ! -f /etc/dsynth/dsynth.ini ]; then
    printf '%s\n' '{name} requires /etc/dsynth/dsynth.ini; rerun shell --refresh from the host' >&2
    exit 1
fi
if [ "$#" -eq 0 ]; then
    if [ -n "${{DPORTS_ORIGIN:-}}" ]; then
        set -- "$DPORTS_ORIGIN"
    elif [ -s "${{DPORTS_TOUCHED_ORIGINS_FILE:-{quote(str(TOUCHED_ORIGINS_PATH))}}}" ]; then
        set --
        while IFS= read -r origin; do
            [ -n "$origin" ] || continue
            set -- "$@" "$origin"
        done < "${{DPORTS_TOUCHED_ORIGINS_FILE:-{quote(str(TOUCHED_ORIGINS_PATH))}}}"
    else
        printf '%s\n' 'usage: {name} ORIGIN... (or run sync-dirty first, or create the env with --origin)' >&2
        exit 1
    fi
fi
exec dsynth -p "$DPORTS_DSYNTH_PROFILE" {subcommand} "$@"
"""
    raise ValueError(f"unknown helper script: {name}")


def helper_signature() -> str:
    digest = hashlib.sha256()
    for name in HELPER_NAMES:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(helper_body(name).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def write_helper_scripts(root_dir, *, bin_dir: str | None = None) -> None:
    bin_dir = root_dir / (bin_dir or "usr/local/bin").lstrip("/")
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in HELPER_NAMES:
        path = bin_dir / name
        path.write_text(helper_body(name))
        path.chmod(0o755)


def build_env_dict(state: EnvironmentState) -> dict[str, str]:
    profile_name = dsynth_profile_name(state)
    helper_bin = HELPER_BIN_DIR
    return {
        "DELTAPORTS_ROOT": "/work/DeltaPorts",
        "FREEBSD_PORTS_ROOT": "/work/freebsd-ports",
        "DPORTS_DEV_ENV": state.name,
        "DPORTS_TARGET": state.target,
        "DPORTS_ORIGIN": state.origin,
        "DPORTS_COMPOSE_ROOT": f"/work/artifacts/compose/{state.target}",
        "DPORTS_LOCK_ROOT": "/work/DPorts",
        "DPORTS_DSYNTH_ROOT": "/work/dsynth",
        "DPORTS_DSYNTH_PROFILE": profile_name,
        "DPORTS_TOUCHED_ORIGINS_FILE": str(TOUCHED_ORIGINS_PATH),
        "DPORTS_HELPER_BIN": helper_bin,
        "DPORTS_ORACLE_PROFILE": state.oracle_profile,
        "DISTDIR": "/usr/distfiles",
        "DPORTS_DOC_USER_GUIDE": "https://github.com/DragonFlyBSD/DeltaPorts/blob/master/docs/dportsv3-user-guide.md",
        "DPORTS_DOC_DEV_ENV": "https://github.com/DragonFlyBSD/DeltaPorts/blob/master/docs/dev-chroot-environment.md",
        "PATH": f"{helper_bin}:/usr/local/bin:/usr/local/sbin:/bin:/sbin:/usr/bin:/usr/sbin",
    }


def write_shell_rc(state: EnvironmentState) -> None:
    env = build_env_dict(state)
    exports = "\n".join(f"export {k}={quote(v)}" for k, v in env.items())
    root_file = state.root_dir / "root/.dports-dev-env.sh"
    root_file.parent.mkdir(parents=True, exist_ok=True)
    root_file.write_text(
        f"""{exports}

if [ -n "$DPORTS_ORIGIN" ] && [ -d "$DPORTS_COMPOSE_ROOT/$DPORTS_ORIGIN" ]; then
    cd "$DPORTS_COMPOSE_ROOT/$DPORTS_ORIGIN"
elif [ -d "$DPORTS_COMPOSE_ROOT" ]; then
    cd "$DPORTS_COMPOSE_ROOT"
else
    cd /work/DeltaPorts
fi

showwelcome() {{
    printf '%s\n' "dports-dev: $DPORTS_DEV_ENV"
    printf '%s\n' "Target: $DPORTS_TARGET"
    if [ -n "$DPORTS_ORIGIN" ]; then
        printf '%s\n' "Origin: $DPORTS_ORIGIN"
    else
        printf '%s\n' 'Origin: full tree'
    fi
    printf '%s\n' 'Paths:'
    printf '  DeltaPorts: %s\n' "$DELTAPORTS_ROOT"
    printf '  FreeBSD ports: %s\n' "$FREEBSD_PORTS_ROOT"
    printf '  DPorts: %s\n' "$DPORTS_LOCK_ROOT"
    printf '  Compose: %s\n' "$DPORTS_COMPOSE_ROOT"
    printf '  dsynth: %s\n' "$DPORTS_DSYNTH_ROOT"
    printf '  dsynth config: %s\n' '/etc/dsynth/dsynth.ini'
    printf '  dsynth profile: %s\n' "$DPORTS_DSYNTH_PROFILE"
    printf '  touched origins: %s\n' "$DPORTS_TOUCHED_ORIGINS_FILE"
    printf '  helper bin: %s\n' "$DPORTS_HELPER_BIN"
    printf '  Distfiles: %s\n' "$DISTDIR"
    if [ -n "$DPORTS_ORIGIN" ]; then
        printf '  Composed origin: %s\n' "$DPORTS_COMPOSE_ROOT/$DPORTS_ORIGIN"
        printf '  Overlay: %s\n' "$DELTAPORTS_ROOT/ports/$DPORTS_ORIGIN"
    fi
    printf '%s\n' 'Docs:'
    printf '  %s\n' "$DPORTS_DOC_USER_GUIDE"
    printf '  %s\n' "$DPORTS_DOC_DEV_ENV"
    printf '%s\n' 'Helpers: regen reapply dbuild showenv'
}}

if [ -n "${{BASH_VERSION:-}}" ]; then
    _dports_label="\\[\\033[1;34m\\]dports-dev\\[\\033[0m\\]"
    _dports_name="\\[\\033[1;32m\\]${{DPORTS_DEV_ENV}}\\[\\033[0m\\]"
    PS1="${{_dports_label}}:${{_dports_name}} \\w\\$ "
else
    _dports_label=$(printf '\\033[1;34mdports-dev\\033[0m')
    _dports_name=$(printf '\\033[1;32m%s\\033[0m' "$DPORTS_DEV_ENV")
    PS1="${{_dports_label}}:${{_dports_name}} # "
fi
export PS1
showwelcome
"""
    )
