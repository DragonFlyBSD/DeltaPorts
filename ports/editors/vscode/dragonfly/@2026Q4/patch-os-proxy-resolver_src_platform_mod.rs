--- os-proxy-resolver/src/platform/mod.rs.orig
+++ os-proxy-resolver/src/platform/mod.rs
@@ -19,7 +19,7 @@
 #[cfg(target_os = "macos")]
 #[path = "macos.rs"]
 mod imp;
-#[cfg(any(target_os = "linux", target_os = "freebsd"))]
+#[cfg(any(target_os = "linux", target_os = "freebsd", target_os = "dragonfly"))]
 #[path = "linux.rs"]
 mod imp;
 #[cfg(windows)]
