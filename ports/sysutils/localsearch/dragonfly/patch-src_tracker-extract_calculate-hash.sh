--- src/tracker-extract/calculate-hash.sh.orig
+++ src/tracker-extract/calculate-hash.sh
@@ -1,2 +1,35 @@
-#!/bin/sh
-cat $@ | sha256sum | cut -f 1 -d ' '
+#!/bin/sh
+set -eu
+
+input=$(mktemp "${TMPDIR:-/tmp}/localsearch-hash.XXXXXXXX")
+trap 'rm -f "$input"' 0
+trap 'exit 1' HUP INT TERM
+
+# Finish reading every input before hashing, so read failures cannot be hidden.
+cat -- "$@" > "$input"
+
+if command -v sha256sum >/dev/null 2>&1; then
+  hash=$(sha256sum < "$input")
+elif command -v sha256 >/dev/null 2>&1; then
+  hash=$(sha256 -q < "$input")
+elif command -v shasum >/dev/null 2>&1; then
+  hash=$(shasum -a 256 < "$input")
+elif command -v digest >/dev/null 2>&1; then
+  hash=$(digest -a sha256 < "$input")
+else
+  echo "calculate-hash.sh: no SHA-256 tool found" >&2
+  exit 1
+fi
+
+hash=${hash%% *}
+case "$hash" in
+  *[!0-9a-fA-F]*)
+    echo "calculate-hash.sh: invalid SHA-256 output" >&2
+    exit 1
+    ;;
+esac
+if [ "${#hash}" -ne 64 ]; then
+  echo "calculate-hash.sh: invalid SHA-256 length" >&2
+  exit 1
+fi
+printf '%s\n' "$hash"
