--- src/libstd/os/dragonfly/raw.rs.orig
+++ src/libstd/os/dragonfly/raw.rs
@@ -43,8 +43,8 @@
     pub st_blocks: blkcnt_t,
     pub st_blksize: blksize_t,
     pub st_flags: fflags_t,
-    pub st_gen: uint32_t,
-    pub st_lspare: int32_t,
-    pub st_qspare1: int64_t,
-    pub st_qspare2: int64_t,
+    pub st_gen: u32,
+    pub st_lspare: i32,
+    pub st_qspare1: i64,
+    pub st_qspare2: i64,
 }
