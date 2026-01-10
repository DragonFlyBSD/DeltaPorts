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

: "${PROFILE:=unknown}"
: "${DIR_LOGS:=}"
: "${DIR_PORTS:=}"
: "${DIR_BUILDBASE:=}"
: "${DIR_PACKAGES:=}"
: "${DIR_REPOSITORY:=}"
: "${DIR_OPTIONS:=}"
: "${DIR_DISTFILES:=}"

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

current_run_dir() {
	# If hook_run_start ran, it writes a file in evidence root.
	# Fall back to evidence root if not present.
	root=$(evidence_root)
	if [ -r "${root}/.current_run" ]; then
		sed -n '1p' "${root}/.current_run" || true
	else
		printf '%s\n' "${root}/loose"
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

enqueue_job() {
	# Args: bundle_dir origin flavor profile run_id ts
	bundle_dir=$1
	origin=$2
	flavor=$3
	profile=$4
	run_id=$5
	ts=$6

	qroot=$(queue_root)
	origin_s=$(sanitize_component "$origin")

	# Build filename, omit flavor if redundant
	fname="${ts}-${profile}-${origin_s}"
	if [ -n "$flavor" ] && [ "$flavor" != "$origin" ] && [ "$flavor" != "${origin%%@*}" ]; then
		flavor_s=$(sanitize_component "$flavor")
		fname="${fname}-@${flavor_s}"
	fi
	fname="${fname}-$$.job"

	# Write to temp file first
	tmpfile="${qroot}/pending/.tmp.$$.${ts}"
	write_kv_file "$tmpfile" \
		"created_ts_utc=${ts}" \
		"profile=${profile}" \
		"origin=${origin}" \
		"flavor=${flavor}" \
		"bundle_dir=${bundle_dir}" \
		"run_id=${run_id}"

	# Atomic move to final location
	mv "$tmpfile" "${qroot}/pending/${fname}"
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
	# Note: use multiple -e to avoid regex quoting issues in /bin/sh.
	RG_ARGS="--no-heading --color never --line-number"

	{
		echo "== Summary =="
		echo "logfile: ${logfile}"
		echo
		echo "== First error candidates (max 60 matches) =="
		rg ${RG_ARGS} -m 60 \
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
		rg ${RG_ARGS} -C 2 \
			-e 'fatal error:' \
			-e 'undefined reference' \
			-e 'ld: error:' \
			-e 'collect2: error' \
			-e 'CMake Error' \
			-e 'configure: error' \
			-e 'meson\.build:.*ERROR' \
			-e '^ninja: build stopped' \
			-e 'error: failed to run custom build command for' \
			-e '^===>\s+Stopped\s+in\s+' \
			"$logfile" || true
		echo
		echo "== Tail (last 200 lines) =="
		tail -n 200 "$logfile" || true
	} >"${outdir}/errors.txt.tmp"

	truncate_bytes "${outdir}/errors.txt.tmp" 200000 "${outdir}/errors.txt"
	rm -f "${outdir}/errors.txt.tmp"
}
