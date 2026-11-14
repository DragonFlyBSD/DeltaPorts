--- src/bootstrap/src/bin/rustc.rs.orig
+++ src/bootstrap/src/bin/rustc.rs
@@ -93,7 +93,7 @@
     let crate_name = parse_value_from_args(&orig_args, "--crate-name");
 
     // When compiling `rustc_driver` remove `-C prefer-dynamic` to allow statically linking `std` into it
-    if crate_name == Some("rustc_driver") {
+    if crate_name == Some("rustc_driver") && !target.unwrap_or(&host).ends_with("-dragonfly") {
         if let Some(pos) = args.iter().enumerate().position(|(i, a)| {
             a == "-C" && args.get(i + 1).map(|a| a == "prefer-dynamic").unwrap_or(false)
         }) {
