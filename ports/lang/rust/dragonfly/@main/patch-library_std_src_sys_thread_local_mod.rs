--- library/std/src/sys/thread_local/mod.rs.orig
+++ library/std/src/sys/thread_local/mod.rs
@@ -77,7 +77,6 @@
             target_os = "redox",
             target_os = "hurd",
             target_os = "netbsd",
-            target_os = "dragonfly"
         ) => {
             mod linux_like;
             mod list;
