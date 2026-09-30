--- vendor/gettext-sys/build.rs.orig
+++ vendor/gettext-sys/build.rs
@@ -120,1 +120,1 @@
-        } else if target.contains("freebsd") {
+        } else if target.contains("freebsd") || target.contains("dragonfly") {
