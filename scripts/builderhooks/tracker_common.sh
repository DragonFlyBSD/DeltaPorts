#!/bin/sh

set -u

HOOK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TRACKER_CONFIG_PATH=${DPORTSV3_TRACKER_CONFIG:-"$HOOK_DIR/dportsv3-tracker.conf"}

if [ -z "${DPORTSV3_BIN:-}" ] && [ -x "$HOOK_DIR/../../dportsv3" ]; then
    DPORTSV3_BIN="$HOOK_DIR/../../dportsv3"
fi

if [ -z "${DPORTSV3_TRACKER_STATE_DIR:-}" ]; then
    DPORTSV3_TRACKER_STATE_DIR="$HOOK_DIR/.tracker-state"
fi

if [ -z "${DPORTSV3_TRACKER_HOOK_LOG:-}" ]; then
    DPORTSV3_TRACKER_HOOK_LOG="$HOOK_DIR/dportsv3-tracker-hooks.log"
fi

tracker_log() {
    mkdir -p -- "$(dirname -- "$DPORTSV3_TRACKER_HOOK_LOG")" 2>/dev/null || true
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$DPORTSV3_TRACKER_HOOK_LOG" 2>/dev/null || true
}

tracker_fail_soft() {
    tracker_log "ERROR: $*"
    exit 0
}

tracker_load_config() {
    if [ -f "$TRACKER_CONFIG_PATH" ]; then
        # shellcheck disable=SC1090
        . "$TRACKER_CONFIG_PATH"
    fi

    : "${PROFILE:=unknown}"

    if [ -z "${DPORTSV3_BIN:-}" ]; then
        tracker_fail_soft "DPORTSV3_BIN is not configured"
    fi
    if [ ! -x "$DPORTSV3_BIN" ]; then
        tracker_fail_soft "DPORTSV3_BIN is not executable: $DPORTSV3_BIN"
    fi
    if [ -z "${DPORTSV3_TRACKER_URL:-}" ]; then
        tracker_fail_soft "DPORTSV3_TRACKER_URL is not configured"
    fi
    if [ -z "${DPORTSV3_TRACKER_TARGET:-}" ]; then
        tracker_fail_soft "DPORTSV3_TRACKER_TARGET is not configured"
    fi
    if [ -z "${DPORTSV3_TRACKER_BUILD_TYPE:-}" ]; then
        DPORTSV3_TRACKER_BUILD_TYPE=test
    fi

    mkdir -p -- "$DPORTSV3_TRACKER_STATE_DIR" 2>/dev/null || true
    TRACKER_STATE_FILE="$DPORTSV3_TRACKER_STATE_DIR/${PROFILE}.env"
}

tracker_load_state() {
    if [ ! -f "$TRACKER_STATE_FILE" ]; then
        tracker_fail_soft "missing tracker state file: $TRACKER_STATE_FILE"
    fi
    # shellcheck disable=SC1090
    . "$TRACKER_STATE_FILE"
    if [ "${TRACKING_DISABLED:-0}" = "1" ]; then
        exit 0
    fi
    if [ -z "${RUN_ID:-}" ]; then
        tracker_fail_soft "tracker state file missing RUN_ID: $TRACKER_STATE_FILE"
    fi
}

tracker_write_state() {
    tmp_file="$TRACKER_STATE_FILE.tmp.$$"
    umask 077
    cat > "$tmp_file" <<EOF
RUN_ID=${RUN_ID}
TARGET=${DPORTSV3_TRACKER_TARGET}
BUILD_TYPE=${DPORTSV3_TRACKER_BUILD_TYPE}
PORTS_QUEUED=${PORTS_QUEUED:-0}
EOF
    mv -f -- "$tmp_file" "$TRACKER_STATE_FILE"
}

tracker_clear_state() {
    rm -f -- "$TRACKER_STATE_FILE"
}

tracker_disable_state() {
    reason=$1
    tmp_file="$TRACKER_STATE_FILE.tmp.$$"
    umask 077
    cat > "$tmp_file" <<EOF
TRACKING_DISABLED=1
EOF
    mv -f -- "$tmp_file" "$TRACKER_STATE_FILE"
    tracker_log "tracking disabled for profile=$PROFILE: $reason"
    exit 0
}

tracker_pkg_version() {
    pkgfile=${PKGNAME##*/}
    pkgfile=${pkgfile%.*}
    printf '%s\n' "${pkgfile##*-}"
}

tracker_run_start() {
    tracker_load_config
    tracker_clear_state

    output=$(
        "$DPORTSV3_BIN" tracker start-build \
            --target "$DPORTSV3_TRACKER_TARGET" \
            --type "$DPORTSV3_TRACKER_BUILD_TYPE" \
            --server "$DPORTSV3_TRACKER_URL" 2>&1
    ) || tracker_disable_state "start-build failed: $output"

    RUN_ID=$(printf '%s\n' "$output" | awk '{print $4}')
    case "$RUN_ID" in
    ''|*[!0-9]*)
        tracker_fail_soft "unable to parse run id from start-build output: $output"
        ;;
    esac

    tracker_write_state
    tracker_log "started tracker run $RUN_ID for profile=$PROFILE target=$DPORTSV3_TRACKER_TARGET type=$DPORTSV3_TRACKER_BUILD_TYPE queued=${PORTS_QUEUED:-0}"
    exit 0
}

tracker_enqueue_one() {
    origin=$1
    version=$2

    tmp_json=$(mktemp "$DPORTSV3_TRACKER_STATE_DIR/enqueue.${PROFILE}.XXXXXX.json") || \
        tracker_fail_soft "failed to allocate temp json file"
    cat > "$tmp_json" <<EOF
[{"origin":"$origin","version":"$version"}]
EOF

    if [ -n "${PORTS_QUEUED:-}" ] && [ "$PORTS_QUEUED" -gt 0 ] 2>/dev/null; then
        output=$(
            "$DPORTSV3_BIN" tracker enqueue-ports \
                --run "$RUN_ID" \
                --file "$tmp_json" \
                --total "$PORTS_QUEUED" \
                --server "$DPORTSV3_TRACKER_URL" 2>&1
        ) || {
            rm -f -- "$tmp_json"
            tracker_fail_soft "enqueue-ports failed for $origin: $output"
        }
    else
        output=$(
            "$DPORTSV3_BIN" tracker enqueue-ports \
                --run "$RUN_ID" \
                --file "$tmp_json" \
                --server "$DPORTSV3_TRACKER_URL" 2>&1
        ) || {
            rm -f -- "$tmp_json"
            tracker_fail_soft "enqueue-ports failed for $origin: $output"
        }
    fi

    rm -f -- "$tmp_json"
}

tracker_mark_building() {
    tracker_load_config
    tracker_load_state

    if [ -z "${ORIGIN:-}" ]; then
        tracker_fail_soft "missing ORIGIN for pkg start hook"
    fi
    if [ -z "${PKGNAME:-}" ]; then
        tracker_fail_soft "missing PKGNAME for pkg start hook"
    fi

    version=$(tracker_pkg_version)
    tracker_enqueue_one "$ORIGIN" "$version"

    output=$(
        "$DPORTSV3_BIN" tracker mark-building \
            --run "$RUN_ID" \
            --origin "$ORIGIN" \
            --server "$DPORTSV3_TRACKER_URL" 2>&1
    ) || tracker_fail_soft "mark-building failed for $ORIGIN: $output"

    tracker_log "marked building run=$RUN_ID origin=$ORIGIN version=$version"
    exit 0
}

tracker_record_result() {
    tracker_load_config
    tracker_load_state

    if [ -z "${ORIGIN:-}" ]; then
        tracker_fail_soft "missing ORIGIN for pkg result hook"
    fi
    if [ -z "${PKGNAME:-}" ]; then
        tracker_fail_soft "missing PKGNAME for pkg result hook"
    fi

    version=$(tracker_pkg_version)
    log_url_arg=
    if [ -n "${DPORTSV3_TRACKER_LOG_URL_BASE:-}" ]; then
        log_url=${DPORTSV3_TRACKER_LOG_URL_BASE%/}/${ORIGIN}
        log_url_arg="--log-url $log_url"
    fi

    if [ -n "$log_url_arg" ]; then
        output=$(
            "$DPORTSV3_BIN" tracker record-result \
                --run "$RUN_ID" \
                --origin "$ORIGIN" \
                --version "$version" \
                --result "$RESULT" \
                --log-url "$log_url" \
                --server "$DPORTSV3_TRACKER_URL" 2>&1
        ) || tracker_fail_soft "record-result failed for $ORIGIN: $output"
    else
        output=$(
            "$DPORTSV3_BIN" tracker record-result \
                --run "$RUN_ID" \
                --origin "$ORIGIN" \
                --version "$version" \
                --result "$RESULT" \
                --server "$DPORTSV3_TRACKER_URL" 2>&1
        ) || tracker_fail_soft "record-result failed for $ORIGIN: $output"
    fi

    tracker_log "recorded result run=$RUN_ID origin=$ORIGIN version=$version result=$RESULT"
    exit 0
}

tracker_run_end() {
    tracker_load_config
    tracker_load_state

    output=$(
        "$DPORTSV3_BIN" tracker finish-build \
            --run "$RUN_ID" \
            --server "$DPORTSV3_TRACKER_URL" 2>&1
    ) || tracker_fail_soft "finish-build failed for run $RUN_ID: $output"

    tracker_log "finished tracker run $RUN_ID for profile=$PROFILE built=${PORTS_BUILT:-0} failed=${PORTS_FAILED:-0} ignored=${PORTS_IGNORED:-0} skipped=${PORTS_SKIPPED:-0}"
    tracker_clear_state
    exit 0
}
