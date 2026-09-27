--- library/std/src/sys/thread_local/mod.rs
+++ library/std/src/sys/thread_local/mod.rs
@@ -59,8 +59,7 @@
             target_os = "fuchsia",
             target_os = "redox",
             target_os = "hurd",
             target_os = "netbsd",
-            target_os = "dragonfly"
         ) => {
             mod linux_like;
             mod list;
