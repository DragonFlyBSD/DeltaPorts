--- src/liblibc/src/unix/bsd/freebsdlike/mod.rs.orig	2016-04-11 21:22:20 UTC
+++ src/liblibc/src/unix/bsd/freebsdlike/mod.rs
@@ -19,10 +19,11 @@ pub enum timezone {}
 
 s! {
     pub struct dirent {
-        pub d_fileno: u32,
-        pub d_reclen: u16,
+        pub d_fileno: u64,
+        pub d_namlen: u16,
         pub d_type: u8,
-        pub d_namlen: u8,
+        pub d_unused1: u8,
+        pub d_unused2: u32,
         pub d_name: [::c_char; 256],
     }
 
