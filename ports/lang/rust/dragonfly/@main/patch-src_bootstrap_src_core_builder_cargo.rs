--- src/bootstrap/src/core/builder/cargo.rs.orig
+++ src/bootstrap/src/core/builder/cargo.rs
@@ -1158,6 +1158,10 @@
         // Keep this scheme in sync with `rustc_metadata::rmeta::decoder`'s
         // `try_to_translate_virtual_to_real`.
         let trim_paths = |cargo: &mut BootstrapCommand, ws_remap: &str| {
+            // Tests use file!() to locate and compare checked-in fixtures at runtime.
+            if cmd_kind == Kind::Test && matches!(mode, Mode::ToolRustcPrivate | Mode::ToolBootstrap) {
+                return;
+            }
             cargo.arg("-Ztrim-paths");
             cargo.arg("--config").arg("profile.release.trim-paths='all'");
             cargo.arg("--config").arg("profile.dev.trim-paths='all'");
@@ -1433,7 +1437,7 @@
         // When we build Rust dylibs they're all intended for intermediate
         // usage, so make sure we pass the -Cprefer-dynamic flag instead of
         // linking all deps statically into the dylib.
-        if matches!(mode, Mode::Std) {
+        if matches!(mode, Mode::Std) || (matches!(mode, Mode::Rustc) && target.triple.ends_with("-dragonfly")) {
             rustflags.arg("-Cprefer-dynamic");
         }
 
