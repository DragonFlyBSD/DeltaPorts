#!/bin/sh
#
# Common helpers for dsynth hook scripts.
#
# dsynth executes hooks with a minimal environment. Do not rely on PATH.
#

set -eu

PATH="/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin"
export PATH

umask 022

# Skip all hook side effects when the caller is the patch agent.
#
# The patch agent runs ``dsynth build`` itself as part of its
# attempt loop (dportsv3/agent/worker.py:dsynth_build). The env
# the agent runs in is the same operator-hooked env (one env per
# target — that's the architecture). Without this guard, every
# agent-driven build failure would fire ``hook_pkg_failure``,
# upload a new bundle, and the runner would enqueue another
# triage job for an origin the agent is already working on.
# That loop is unbounded in the worst case (one fix attempt
# unmasks ten new failures, each of which spawns a new agent
# loop) and pure waste in the best case (the agent already
# knows its build failed; the bundle is redundant).
#
# Earlier this used a ``DPORTSV3_HOOKS_DISABLED=1`` env var, but
# dsynth strips arbitrary env vars before invoking hooks (it
# passes only its known set: PROFILE, DIR_LOGS, etc.). The var
# never reached the hook. A sentinel file on the writable overlay
# is dsynth-proof — the filesystem state is the same regardless
# of what dsynth does to the environment.
#
# The agent creates this file before ``dsynth build`` and removes
# it on EXIT (trap). If the agent process dies uncatchably (kill
# -9), the operator must ``rm`` it by hand — otherwise the next
# legitimate operator dsynth would skip its hooks too.
_dports_hooks_flag="${DPORTSV3_HOOKS_FLAG_FILE:-/work/.dports-agent-hooks-disabled}"
if [ -f "$_dports_hooks_flag" ]; then
	exit 0
fi
unset _dports_hooks_flag

# Source the operator config first so its values win over the defaults
# below. The path is overridable via DPORTSV3_HOOKS_CONFIG; default is
# the conf installed next to this script (typically
# /etc/dsynth/dportsv3-hooks.conf inside the chroot).
DPORTSV3_HOOKS_CONFIG=${DPORTSV3_HOOKS_CONFIG:-"$(dirname "$0")/dportsv3-hooks.conf"}
if [ -f "$DPORTSV3_HOOKS_CONFIG" ]; then
	# shellcheck disable=SC1090
	. "$DPORTSV3_HOOKS_CONFIG"
fi

: "${PROFILE:=unknown}"
: "${DIR_LOGS:=}"
: "${DIR_PORTS:=}"
: "${DIR_BUILDBASE:=}"
: "${DIR_PACKAGES:=}"
: "${DIR_REPOSITORY:=}"
: "${DIR_OPTIONS:=}"
: "${DIR_DISTFILES:=}"

: "${ARTIFACT_STORE_URL:=http://127.0.0.1:8788}"
: "${ARTIFACT_STORE_CLIENT:=/build/synth/DeltaPorts/scripts/artifact-store-client}"

hook_config_dir() {
	# Hooks live in ConfigBase (/etc/dsynth or /usr/local/etc/dsynth).
	dir=$(dirname "$0")
	# best effort to canonicalize
	case "$dir" in
	/*) printf '%s\n' "$dir" ;;
	*) printf '%s\n' "$(pwd)/$dir" ;;
	esac
}

now_utc() {
	# YYYYmmdd-HHMMSSZ
	date -u "+%Y%m%d-%H%M%SZ"
}

sanitize_component() {
	# Replace characters that are annoying in filenames.
	# Keep it deterministic and readable.
	printf '%s' "$1" | tr '/:@' '___' | tr -cd 'A-Za-z0-9._-'
}

origin_cat() {
	printf '%s' "$1" | sed 's,/.*$,,'
}

origin_port() {
	printf '%s' "$1" | sed 's,^.*/,,'
}

logfile_for_origin() {
	# Reconstruct dsynth per-port log filename.
	# dsynth uses: ${DIR_LOGS}/${category}___${portname}${WorkerFlavorPrt}.log
	# where WorkerFlavorPrt is "" or "@flavor".
	origin_raw=$1
	flavor_raw=${2:-}

	origin_base=${origin_raw%%@*}
	cat=$(origin_cat "$origin_base")
	port=$(origin_port "$origin_base")
	base="${cat}___${port}"

	# dsynth sets FLAVOR=$ORIGIN when no flavor; only add @ when different.
	if [ -n "$flavor_raw" ] && [ "$flavor_raw" != "$origin_raw" ] && [ "$flavor_raw" != "$origin_base" ]; then
		base="${base}@${flavor_raw}"
	fi

	printf '%s\n' "${DIR_LOGS}/${base}.log"
}

current_run_id() {
	# hook_run_start writes a run_id into evidence root.
	root=$(evidence_root)
	if [ -r "${root}/.current_run" ]; then
		sed -n '1p' "${root}/.current_run" || true
	else
		printf '%s\n' "run-${PROFILE}-unknown"
	fi
}

evidence_root() {
	if [ -z "$DIR_LOGS" ]; then
		# Last resort
		printf '%s\n' "/tmp/dsynth-evidence"
		return
	fi
	printf '%s\n' "${DIR_LOGS}/evidence"
}

queue_root() {
	printf '%s\n' "$(evidence_root)/queue"
}

ensure_queue_dirs() {
	qroot=$(queue_root)
	mkdir -p "${qroot}/pending"
	mkdir -p "${qroot}/inflight"
	mkdir -p "${qroot}/done"
	mkdir -p "${qroot}/failed"
}

artifact_store() {
	"${ARTIFACT_STORE_CLIENT}" --url "${ARTIFACT_STORE_URL}" "$@"
}

require_artifact_store() {
	artifact_store health >/dev/null
}

# Check if this is a rebuild attempt (branch starts with ai-fix/)
# Returns iteration number (0 if not a rebuild attempt)
detect_rebuild_iteration() {
	# If tracking context exists, use it regardless of branch
	evidence=$(evidence_root)
	ctx_file="${evidence}/.current_apply_context"
	if [ -r "$ctx_file" ]; then
		iter=$(grep '^iteration=' "$ctx_file" 2>/dev/null | head -1 | cut -d= -f2)
		if [ -n "$iter" ]; then
			printf '%d\n' $((iter + 1))
			return
		fi
	fi

	deltaports_dir="${DIR_PORTS%/DPorts*}/DeltaPorts"

	# Check if DeltaPorts directory exists
	if [ ! -d "$deltaports_dir" ]; then
		printf '0\n'
		return
	fi

	# Get current branch
	current_branch=$(cd "$deltaports_dir" && git branch --show-current 2>/dev/null || true)

	# Check if it's an AI fix branch
	case "$current_branch" in
	ai-fix/*)
		# Default to iteration 2 if we're on ai-fix branch but no context
		printf '2\n'
		;;
	*)
		printf '0\n'
		;;
	esac
}

# Get previous bundle path from tracking context (for rebuild attempts)
get_previous_bundle() {
	evidence=$(evidence_root)
	ctx_file="${evidence}/.current_apply_context"

	if [ -r "$ctx_file" ]; then
		grep '^previous_bundle=' "$ctx_file" 2>/dev/null | head -1 | cut -d= -f2
	fi
}

enqueue_job() {
	# Args: bundle_id origin flavor profile run_id ts
	bundle_id=$1
	origin=$2
	flavor=$3
	profile=$4
	run_id=$5
	ts=$6

	qroot=$(queue_root)
	origin_s=$(sanitize_component "$origin")

	# Check if this is a rebuild attempt
	iteration=$(detect_rebuild_iteration)
	previous_bundle=$(get_previous_bundle)

	# Build filename, omit flavor if redundant
	fname="${ts}-${profile}-${origin_s}"
	if [ -n "$flavor" ] && [ "$flavor" != "$origin" ] && [ "$flavor" != "${origin%%@*}" ]; then
		flavor_s=$(sanitize_component "$flavor")
		fname="${fname}-@${flavor_s}"
	fi

	# Add iteration suffix if this is a retry
	if [ "$iteration" -gt 1 ]; then
		fname="${fname}-iter${iteration}"
	fi

	fname="${fname}-$$.job"

	# Write to temp file first
	tmpfile="${qroot}/pending/.tmp.$$.${ts}"

	# Base job fields. target comes from DPORTSV3_TRACKER_TARGET if
	# set (loaded by tracker_load_config); empty otherwise — that
	# leaves jobs.target NULL, which step 5 read endpoints surface
	# as "unknown" under target filters.
	write_kv_file "$tmpfile" \
		"created_ts_utc=${ts}" \
		"profile=${profile}" \
		"target=${DPORTSV3_TRACKER_TARGET:-}" \
		"origin=${origin}" \
		"flavor=${flavor}" \
		"bundle_id=${bundle_id}" \
		"run_id=${run_id}" \
		"type=triage" \
		"snippet_round=0" \
		"has_snippets=false"

	# Add iteration tracking if this is a retry
	if [ "$iteration" -gt 1 ]; then
		printf 'iteration=%d\n' "$iteration" >>"$tmpfile"
		printf 'max_iterations=3\n' >>"$tmpfile"
		if [ -n "$previous_bundle" ]; then
			printf 'previous_bundle=%s\n' "$previous_bundle" >>"$tmpfile"
		fi
	fi

	# Atomic move to final location
	mv "$tmpfile" "${qroot}/pending/${fname}"

	# Fire the HOOK_ENQUEUED lifecycle event so the UI sees the new
	# job. Best-effort: store outages don't block enqueue (the .job
	# file is the source of truth; the runner emits a CLAIM event on
	# pickup and the metadata flows via the detail block on this
	# initial transition).
	artifact_store job-transition \
		--job-id "$fname" \
		--event hook_enqueued \
		--actor hook \
		--type triage \
		--origin "$origin" \
		--flavor "$flavor" \
		--created-ts-utc "$ts" \
		--path "${qroot}/pending/${fname}" \
		--target "${DPORTSV3_TRACKER_TARGET:-}" \
		--bundle-id "$bundle_id" \
		>/dev/null 2>&1 || true
}

write_kv_file() {
	out=$1
	shift
	: >"$out"
	for kv in "$@"; do
		printf '%s\n' "$kv" >>"$out"
	done
}

copy_if_exists() {
	src=$1
	dst=$2
	if [ -e "$src" ]; then
		mkdir -p "$(dirname "$dst")"
		cp -p "$src" "$dst"
	fi
}

copy_glob_if_exists() {
	srcdir=$1
	pattern=$2
	dstdir=$3
	if [ -d "$srcdir" ]; then
		mkdir -p "$dstdir"
		# shellcheck disable=SC2035
		for f in "$srcdir"/$pattern; do
			[ -e "$f" ] || continue
			cp -p "$f" "$dstdir/"
		done
	fi
}

truncate_bytes() {
	infile=$1
	max_bytes=$2
	outfile=$3

	# Preserve whole file if already under limit.
	size=$(wc -c <"$infile" | tr -d ' ')
	if [ "$size" -le "$max_bytes" ]; then
		cp -p "$infile" "$outfile"
		return
	fi

	dd if="$infile" of="$outfile" bs=1 count="$max_bytes" 2>/dev/null
	printf '\n[...truncated to %s bytes...]\n' "$max_bytes" >>"$outfile"
}

# -----------------------------------------------------------------------------
# dportsv3 tracker integration (was scripts/builderhooks/tracker_common.sh)
# -----------------------------------------------------------------------------
#
# Tracker integration is optional but default-on. Operator installs
# `dportsv3-hooks.conf` next to this file (or sets DPORTSV3_HOOKS_CONFIG)
# with at least DPORTSV3_TRACKER_URL + DPORTSV3_BIN, then every hook
# records per-port outcomes via `dportsv3 tracker`. Hooks soft-fail on
# tracker outages so dsynth keeps building.
#
# When DPORTSV3_TRACKER_URL is unset (no config or commented out),
# every tracker_* high-level call short-circuits with no side effects.
# (DPORTSV3_HOOKS_CONFIG was already resolved and sourced near the
# top of this file so all variables — not just tracker_* ones — get
# the operator's overrides before any default kicks in.)

tracker_log() {
	: "${DPORTSV3_TRACKER_HOOK_LOG:=${DIR_LOGS:-/tmp}/dportsv3-hooks.log}"
	mkdir -p -- "$(dirname -- "$DPORTSV3_TRACKER_HOOK_LOG")" 2>/dev/null || true
	printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" \
		>> "$DPORTSV3_TRACKER_HOOK_LOG" 2>/dev/null || true
}

tracker_fail_soft() {
	tracker_log "ERROR: $*"
	exit 0
}

tracker_should_skip() {
	# Returns 0 (true) if tracker should be skipped — config missing or
	# DPORTSV3_TRACKER_URL not set. Callers can guard their work with:
	#     tracker_should_skip && return 0
	[ ! -f "$DPORTSV3_HOOKS_CONFIG" ] && return 0
	# shellcheck disable=SC1090
	. "$DPORTSV3_HOOKS_CONFIG"
	[ -z "${DPORTSV3_TRACKER_URL:-}" ] && return 0
	return 1
}

tracker_load_config() {
	# Idempotent: safe to call multiple times. Sets defaults for any
	# unset values. Soft-fails with a clear message when required values
	# can't be derived.
	if [ -f "$DPORTSV3_HOOKS_CONFIG" ]; then
		# shellcheck disable=SC1090
		. "$DPORTSV3_HOOKS_CONFIG"
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

	# Default target = @${PROFILE} (per the "one profile per target" policy).
	# If operator already set the value, keep it.
	if [ -z "${DPORTSV3_TRACKER_TARGET:-}" ]; then
		case "$PROFILE" in
		@*) DPORTSV3_TRACKER_TARGET=$PROFILE ;;
		*)  DPORTSV3_TRACKER_TARGET="@$PROFILE" ;;
		esac
	fi
	: "${DPORTSV3_TRACKER_BUILD_TYPE:=test}"

	# Per-profile state file lives under evidence/.tracker-state by
	# default so it's colocated with the artifact-store evidence tree.
	: "${DPORTSV3_TRACKER_STATE_DIR:=$(evidence_root)/.tracker-state}"
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

tracker_enqueue_one() {
	origin=$1
	version=$2

	tmp_json=$(mktemp "$DPORTSV3_TRACKER_STATE_DIR/enqueue.${PROFILE}.XXXXXX") || \
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

tracker_run_start() {
	tracker_should_skip && return 0
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
	tracker_log "started tracker run $RUN_ID for profile=$PROFILE target=$DPORTSV3_TRACKER_TARGET type=$DPORTSV3_TRACKER_BUILD_TYPE"
	return 0
}

tracker_mark_building() {
	tracker_should_skip && return 0
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
	return 0
}

tracker_record_result() {
	# Args: result (pass | fail | skipped | ignored)
	tracker_should_skip && return 0
	tracker_load_config
	tracker_load_state

	result_arg=$1
	if [ -z "${ORIGIN:-}" ]; then
		tracker_fail_soft "missing ORIGIN for pkg result hook"
	fi
	if [ -z "${PKGNAME:-}" ]; then
		tracker_fail_soft "missing PKGNAME for pkg result hook"
	fi

	version=$(tracker_pkg_version)

	if [ -n "${DPORTSV3_TRACKER_LOG_URL_BASE:-}" ]; then
		log_url=${DPORTSV3_TRACKER_LOG_URL_BASE%/}/${ORIGIN}
		output=$(
			"$DPORTSV3_BIN" tracker record-result \
				--run "$RUN_ID" \
				--origin "$ORIGIN" \
				--version "$version" \
				--result "$result_arg" \
				--log-url "$log_url" \
				--server "$DPORTSV3_TRACKER_URL" 2>&1
		) || tracker_fail_soft "record-result failed for $ORIGIN: $output"
	else
		output=$(
			"$DPORTSV3_BIN" tracker record-result \
				--run "$RUN_ID" \
				--origin "$ORIGIN" \
				--version "$version" \
				--result "$result_arg" \
				--server "$DPORTSV3_TRACKER_URL" 2>&1
		) || tracker_fail_soft "record-result failed for $ORIGIN: $output"
	fi

	tracker_log "recorded result run=$RUN_ID origin=$ORIGIN version=$version result=$result_arg"
	return 0
}

tracker_run_end() {
	tracker_should_skip && return 0
	tracker_load_config
	tracker_load_state

	output=$(
		"$DPORTSV3_BIN" tracker finish-build \
			--run "$RUN_ID" \
			--server "$DPORTSV3_TRACKER_URL" 2>&1
	) || tracker_fail_soft "finish-build failed for run $RUN_ID: $output"

	tracker_log "finished tracker run $RUN_ID for profile=$PROFILE"
	tracker_clear_state
	return 0
}

distill_log() {
	logfile=$1
	outdir=$2

	mkdir -p "$outdir"

	if [ ! -r "$logfile" ]; then
		write_kv_file "${outdir}/errors.txt" "missing_log=1" "logfile=${logfile}"
		return
	fi

	# High-signal patterns. Keep them fairly conservative to avoid dumping
	# thousands of harmless "error:" hits.
	# Note: use grep -nE (extended-regex, line-number); rg isn't in
	# dfly base. \s+ → [[:space:]]+ for POSIX ERE compatibility.

	{
		echo "== Summary =="
		echo "logfile: ${logfile}"
		echo
		echo "== First error candidates (max 60 matches) =="
		grep -nE -m 60 \
			-e 'fatal error:' \
			-e 'undefined reference' \
			-e 'ld: error:' \
			-e 'collect2: error' \
			-e 'CMake Error' \
			-e 'configure: error' \
			-e 'meson\.build:.*ERROR' \
			-e '^ninja: build stopped' \
			-e 'error: failed to run custom build command for' \
			-e 'ERROR: ' \
			-e 'No such file or directory' \
			"$logfile" || true
		echo
		echo "== Error blocks (context +/-2, truncated later) =="
		grep -nE -C 2 \
			-e 'fatal error:' \
			-e 'undefined reference' \
			-e 'ld: error:' \
			-e 'collect2: error' \
			-e 'CMake Error' \
			-e 'configure: error' \
			-e 'meson\.build:.*ERROR' \
			-e '^ninja: build stopped' \
			-e 'error: failed to run custom build command for' \
			-e '^===>[[:space:]]+Stopped[[:space:]]+in[[:space:]]+' \
			"$logfile" || true
		echo
		echo "== Tail (last 200 lines) =="
		tail -n 200 "$logfile" || true
	} >"${outdir}/errors.txt.tmp"

	truncate_bytes "${outdir}/errors.txt.tmp" 200000 "${outdir}/errors.txt"
	rm -f "${outdir}/errors.txt.tmp"
}
