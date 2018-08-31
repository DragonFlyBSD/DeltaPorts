--- src/libunwind/build.rs.orig	2018-03-25 14:26:14 UTC
+++ src/libunwind/build.rs
@@ -33,7 +33,8 @@ fn main() {
     } else if target.contains("bitrig") {
         println!("cargo:rustc-link-lib=c++abi");
     } else if target.contains("dragonfly") {
-        println!("cargo:rustc-link-lib=gcc_pic");
+        println!("cargo:rustc-link-lib=gcc_s");
+        println!("cargo:rustc-link-search=@GCCSPATH@");
     } else if target.contains("windows-gnu") {
         println!("cargo:rustc-link-lib=static-nobundle=gcc_eh");
         println!("cargo:rustc-link-lib=static-nobundle=pthread");
