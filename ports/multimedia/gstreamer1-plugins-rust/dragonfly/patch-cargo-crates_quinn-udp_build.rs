--- cargo-crates/quinn-udp-0.5.14/build.rs.orig
+++ cargo-crates/quinn-udp-0.5.14/build.rs
@@ -16,6 +16,7 @@
         bsd: {
             any(
                 target_os = "freebsd",
+                target_os = "dragonfly",
                 target_os = "openbsd",
                 target_os = "netbsd"
             )
