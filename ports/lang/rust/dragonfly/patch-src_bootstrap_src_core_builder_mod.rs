--- src/bootstrap/src/core/builder/mod.rs.orig	2025-01-27 23:20:59 UTC
+++ src/bootstrap/src/core/builder/mod.rs
@@ -1121,7 +1121,7 @@ impl<'a> Builder<'a> {
     /// Returns if `std` should be statically linked into `rustc_driver`.
     /// It's currently not done on `windows-gnu` due to linker bugs.
     pub fn link_std_into_rustc_driver(&self, target: TargetSelection) -> bool {
-        !target.triple.ends_with("-windows-gnu")
+        !target.triple.ends_with("-windows-gnu") && !target.triple.ends_with("dragonfly")
     }

     /// Obtain a compiler at a given stage and for a given host (i.e., this is the target that the
