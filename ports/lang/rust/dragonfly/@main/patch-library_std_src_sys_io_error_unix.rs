--- library/std/src/sys/io/error/unix.rs.orig
+++ library/std/src/sys/io/error/unix.rs
@@ -5,7 +5,6 @@
 
 unsafe extern "C" {
     #[cfg(not(any(
-        target_os = "dragonfly",
         target_os = "vxworks",
         target_os = "rtems",
         target_os = "wasi"
@@ -13,6 +12,7 @@
     #[cfg_attr(
         any(
             target_os = "linux",
+            target_os = "dragonfly",
             target_os = "emscripten",
             target_os = "fuchsia",
             target_os = "l4re",
