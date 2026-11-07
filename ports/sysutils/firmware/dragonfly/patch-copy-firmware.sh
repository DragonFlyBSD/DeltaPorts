--- copy-firmware.sh.orig	2026-06-23 04:38:41.000000000 +0800
+++ copy-firmware.sh	2026-07-28 12:44:02.128472000 +0800
@@ -143,7 +143,7 @@
 grep -E '^Link:' WHENCE | sed -e 's/^Link: *//g;s/-> //g' | while read l t; do
     directory="$destdir/$(dirname "$l")"
     install -d "$directory"
-    target="$(cd "$directory" && realpath -m -s "$t")"
+    target="$directory/$t"
     if test -e "$target"; then
         $verbose "creating link $l -> $t"
         if [ "$num_jobs" -gt 1 ]; then
@@ -165,8 +165,11 @@
 fi
 
 # Verify no broken symlinks
-if test "$(find "$destdir" -xtype l | wc -l)" -ne 0 ; then
-    err "Broken symlinks found:\n$(find "$destdir" -xtype l)"
+broken_links="$(find "$destdir" -type l | while read -r link; do
+    test -e "$link" || printf '%s\n' "$link"
+done)"
+if test -n "$broken_links" ; then
+    err "Broken symlinks found:\n$broken_links"
 fi
 
 exit 0
