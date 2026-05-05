from __future__ import annotations

import shlex
import hashlib

from .state import EnvironmentState


def quote(value: str) -> str:
    return shlex.quote(value)


HELPER_NAMES = ["regen", "reapply", "showenv", "dbuild"]


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
        return """#!/bin/sh
set -eu
: "${DPORTS_TARGET:?reapply: DPORTS_TARGET is not set; run from a dports-dev shell}"
: "${DPORTS_COMPOSE_ROOT:?reapply: DPORTS_COMPOSE_ROOT is not set}"
: "${DPORTS_ORACLE_PROFILE:=off}"
if [ -z "${DPORTS_ORIGIN:-}" ]; then
    printf '%s\n' 'reapply requires the environment to have been created with --origin' >&2
    exit 1
fi
exec /work/DeltaPorts/dportsv3 dsl apply "/work/DeltaPorts/ports/$DPORTS_ORIGIN/overlay.dops" --port-root "$DPORTS_COMPOSE_ROOT/$DPORTS_ORIGIN" --target "$DPORTS_TARGET" --oracle-profile "$DPORTS_ORACLE_PROFILE"
"""
    if name == "showenv":
        return """#!/bin/sh
env | grep '^DPORTS_' | sort
"""
    if name == "dbuild":
        return """#!/bin/sh
set -eu
: "${DPORTS_TARGET:?dbuild: DPORTS_TARGET is not set; run from a dports-dev shell}"
: "${DPORTS_DSYNTH_PROFILE:=DPortsDev}"
if ! command -v dsynth >/dev/null 2>&1; then
    printf '%s\n' 'dbuild requires dsynth; recreate the env with dsynth available' >&2
    exit 1
fi
if [ ! -f /etc/dsynth/dsynth.ini ]; then
    printf '%s\n' 'dbuild requires /etc/dsynth/dsynth.ini; rerun shell --refresh from the host' >&2
    exit 1
fi
if [ "$#" -eq 0 ]; then
    if [ -n "${DPORTS_ORIGIN:-}" ]; then
        set -- "$DPORTS_ORIGIN"
    else
        printf '%s\n' 'usage: dbuild ORIGIN... (or create the env with --origin)' >&2
        exit 1
    fi
fi
exec dsynth -p "$DPORTS_DSYNTH_PROFILE" build "$@"
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


def write_helper_scripts(root_dir) -> None:
    bin_dir = root_dir / "usr/local/bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in HELPER_NAMES:
        path = bin_dir / name
        path.write_text(helper_body(name))
        path.chmod(0o755)


def write_shell_rc(state: EnvironmentState) -> None:
    root_file = state.root_dir / "root/.dports-dev-env.sh"
    root_file.parent.mkdir(parents=True, exist_ok=True)
    root_file.write_text(
        f"""export DELTAPORTS_ROOT=/work/DeltaPorts
export FREEBSD_PORTS_ROOT=/work/freebsd-ports
export DPORTS_DEV_ENV={quote(state.name)}
export DPORTS_TARGET={quote(state.target)}
export DPORTS_ORIGIN={quote(state.origin)}
export DPORTS_COMPOSE_ROOT={quote(f'/work/artifacts/compose/{state.target}')}
export DPORTS_LOCK_ROOT=/work/DPorts
export DPORTS_DSYNTH_ROOT=/work/dsynth
export DPORTS_DSYNTH_PROFILE=DPortsDev
export DPORTS_ORACLE_PROFILE={quote(state.oracle_profile)}
export DISTDIR=/usr/distfiles
export DPORTS_DOC_USER_GUIDE=https://github.com/DragonFlyBSD/DeltaPorts/blob/master/docs/dportsv3-user-guide.md
export DPORTS_DOC_DEV_ENV=https://github.com/DragonFlyBSD/DeltaPorts/blob/master/docs/dev-chroot-environment.md
export PATH=/usr/local/bin:/usr/local/sbin:/bin:/sbin:/usr/bin:/usr/sbin

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
