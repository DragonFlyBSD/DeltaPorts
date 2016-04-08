--- src/libstd/os/dragonfly/raw.rs.orig	2016-03-18 13:54:58 UTC
+++ src/libstd/os/dragonfly/raw.rs
@@ -37,13 +37,15 @@ use os::raw::c_long;
 #[stable(feature = "raw_ext", since = "1.1.0")]
 pub struct stat {
     #[stable(feature = "raw_ext", since = "1.1.0")]
-    pub st_dev: u32,
+    pub st_ino: u64,
+    #[stable(feature = "raw_ext", since = "1.1.0")]
+    pub st_nlink: u32,
     #[stable(feature = "raw_ext", since = "1.1.0")]
-    pub st_ino: u32,
+    pub st_dev: u32,
     #[stable(feature = "raw_ext", since = "1.1.0")]
     pub st_mode: u16,
     #[stable(feature = "raw_ext", since = "1.1.0")]
-    pub st_nlink: u16,
+    pub st_padding1: u16,
     #[stable(feature = "raw_ext", since = "1.1.0")]
     pub st_uid: u32,
     #[stable(feature = "raw_ext", since = "1.1.0")]
@@ -75,7 +77,7 @@ pub struct stat {
     #[stable(feature = "raw_ext", since = "1.1.0")]
     pub st_lspare: i32,
     #[stable(feature = "raw_ext", since = "1.1.0")]
-    pub st_birthtime: c_long,
+    pub st_qspare1: i64,
     #[stable(feature = "raw_ext", since = "1.1.0")]
-    pub st_birthtime_nsec: c_long,
+    pub st_qspare2: i64,
 }
