--- library/backtrace/src/symbolize/gimli.rs	2025-03-16 00:27:19.000000000 +0800
+++ library/backtrace/src/symbolize/gimli.rs	2026-06-11 14:26:09.092017000 +0800
@@ -35,6 +35,7 @@
     } else if #[cfg(any(
         target_os = "android",
         target_os = "freebsd",
+        target_os = "dragonfly",
         target_os = "fuchsia",
         target_os = "haiku",
         target_os = "hurd",
@@ -220,6 +221,7 @@
             target_os = "linux",
             target_os = "fuchsia",
             target_os = "freebsd",
+            target_os = "dragonfly",
             target_os = "hurd",
             target_os = "openbsd",
             target_os = "netbsd",
