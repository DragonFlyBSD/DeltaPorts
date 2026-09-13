--- cargo-crates/glycin-3.1.0/src/api_common.rs.orig
+++ cargo-crates/glycin-3.1.0/src/api_common.rs
@@ -21,6 +21,10 @@
 
 impl SandboxMechanism {
     pub async fn detect() -> Self {
+        #[cfg(target_os = "dragonfly")]
+        return Self::NotSandboxed;
+
+        #[cfg(not(target_os = "dragonfly"))]
         match RunEnvironment::cached().await {
             RunEnvironment::FlatpakDevel => Self::NotSandboxed,
             RunEnvironment::Flatpak => Self::FlatpakSpawn,
