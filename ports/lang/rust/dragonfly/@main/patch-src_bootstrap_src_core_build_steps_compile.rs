--- src/bootstrap/src/core/build_steps/compile.rs.orig
+++ src/bootstrap/src/core/build_steps/compile.rs
@@ -2369,7 +2369,7 @@
 
             // `rustc_driver` statically links to stdlib, so do not copy the libstd dynamic library file
             let can_be_rustc_dynamic_dep =
-                !(filename.starts_with("std-") || filename.starts_with("libstd-"));
+                target_compiler.host.triple.ends_with("-dragonfly") || !(filename.starts_with("std-") || filename.starts_with("libstd-"));
 
             if is_dylib_or_debug && can_be_rustc_dynamic_dep && !is_proc_macro {
                 builder.copy_link(&f.path(), &rustc_libdir.join(&filename), FileType::Regular);
