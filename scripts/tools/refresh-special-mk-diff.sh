#!/usr/bin/env bash
#
# refresh-special-mk-diff.sh — Carry forward a special/Mk diff to a new quarter.
#
# Uses 3-way merge (gdiff3) to replay the DeltaPorts change from an old
# upstream quarter onto a new upstream quarter, fetching both base files
# from FreeBSD cgit.  No local FreeBSD checkouts needed.
#
# Usage:
#   ./refresh-special-mk-diff.sh <Mk-relative-file> <old-quarter> <new-quarter> \
#       <old-diff-path> <new-diff-path>
#
# Example:
#   ./refresh-special-mk-diff.sh Uses/gstreamer.mk 2025Q2 2026Q1 \
#       special/Mk/diffs/Uses_gstreamer.diff \
#       special/Mk/diffs/@2026Q1/Uses_gstreamer.diff
#
set -euo pipefail

CGIT_BASE="https://cgit.freebsd.org/ports/plain/Mk"

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  echo "usage: $0 <Mk-relative-file> <old-quarter> <new-quarter> <old-diff> <new-diff>"
  echo
  echo "arguments:"
  echo "  Mk-relative-file   file path relative to Mk/ (e.g. Uses/gstreamer.mk, bsd.port.mk)"
  echo "  old-quarter         FreeBSD branch the old diff was written against (e.g. 2025Q2)"
  echo "  new-quarter         FreeBSD branch to carry the change forward to (e.g. 2026Q1)"
  echo "  old-diff            path to the existing unscoped special/Mk diff"
  echo "  new-diff            path to write the new target-scoped diff"
  exit 1
}

# --- prerequisites ---

if ! command -v gdiff3 >/dev/null 2>&1; then
  die "gdiff3 not found; install GNU diffutils (pkg install diffutils)"
fi

if ! command -v curl >/dev/null 2>&1; then
  die "curl not found"
fi

if ! command -v patch >/dev/null 2>&1; then
  die "patch not found"
fi

# --- args ---

[ "$#" -eq 5 ] || usage

file="$1"
oldq="$2"
newq="$3"
olddiff="$4"
newdiff="$5"

[ -f "$olddiff" ] || die "old diff not found: $olddiff"

# --- workspace ---

tmp="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

mkdir -p "$tmp/oldtree/$(dirname "$file")" \
         "$tmp/newtree/$(dirname "$file")" \
         "$(dirname "$newdiff")"

# --- step 1: fetch old upstream base and apply existing patch ---

echo "==> Fetching old base: Mk/$file from $oldq"
curl -fsSL "$CGIT_BASE/$file?h=$oldq" -o "$tmp/oldtree/$file"
cp "$tmp/oldtree/$file" "$tmp/old-base"

echo "==> Applying old DeltaPorts patch"
if ! patch --batch --forward -V none -r - -d "$tmp/oldtree" -p0 -i "$(realpath "$olddiff")"; then
  die "old patch does not apply cleanly against $oldq — wrong old-quarter?"
fi
cp "$tmp/oldtree/$file" "$tmp/old-patched"

# --- step 2: fetch new upstream base ---

echo "==> Fetching new base: Mk/$file from $newq"
curl -fsSL "$CGIT_BASE/$file?h=$newq" -o "$tmp/newtree/$file"
cp "$tmp/newtree/$file" "$tmp/new-base"

# --- step 3: 3-way merge ---

echo "==> Carrying forward with gdiff3"
if ! gdiff3 -m "$tmp/new-base" "$tmp/old-base" "$tmp/old-patched" > "$tmp/new-patched"; then
  echo
  echo "3-way merge produced conflicts."
  echo
  echo "Resolve conflict markers in:"
  echo "  $tmp/new-patched"
  echo
  echo "Then regenerate and validate manually:"
  echo "  diff -u --label '$file' --label '$file' '$tmp/new-base' '$tmp/new-patched' > '$newdiff'"
  echo "  patch --dry-run --batch --forward -V none -r - -d '$tmp/newtree' -p0 -i '$newdiff'"
  echo
  # Keep temp dir around for manual resolution
  trap - EXIT
  exit 1
fi

# --- step 4: check if patch is still needed ---

if cmp -s "$tmp/new-base" "$tmp/new-patched"; then
  echo "==> No patch needed: $newq already contains this change"
  exit 0
fi

# --- step 5: generate and validate new diff ---

echo "==> Writing new quarter patch"
diff -u --label "$file" --label "$file" \
  "$tmp/new-base" "$tmp/new-patched" > "$newdiff" || true

echo "==> Validating patch against fetched $newq tree"
patch --dry-run --batch --forward -V none -r - -d "$tmp/newtree" -p0 -i "$(realpath "$newdiff")"

echo
echo "OK: $newdiff"
