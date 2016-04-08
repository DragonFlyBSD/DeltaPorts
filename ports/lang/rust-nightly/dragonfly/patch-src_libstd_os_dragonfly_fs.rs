--- src/libstd/os/dragonfly/fs.rs.orig	2016-03-18 13:54:58 UTC
+++ src/libstd/os/dragonfly/fs.rs
@@ -35,13 +35,13 @@ pub trait MetadataExt {
     fn as_raw_stat(&self) -> &raw::stat;
 
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
-    fn st_dev(&self) -> u64;
+    fn st_dev(&self) -> u32;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
     fn st_ino(&self) -> u64;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
     fn st_mode(&self) -> u32;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
-    fn st_nlink(&self) -> u64;
+    fn st_nlink(&self) -> u32;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
     fn st_uid(&self) -> u32;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
@@ -63,9 +63,9 @@ pub trait MetadataExt {
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
     fn st_ctime_nsec(&self) -> i64;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
-    fn st_birthtime(&self) -> i64;
+    fn st_qspare1(&self) -> i64;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
-    fn st_birthtime_nsec(&self) -> i64;
+    fn st_qspare2(&self) -> i64;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
     fn st_blksize(&self) -> u64;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
@@ -76,6 +76,8 @@ pub trait MetadataExt {
     fn st_gen(&self) -> u32;
     #[stable(feature = "metadata_ext2", since = "1.8.0")]
     fn st_lspare(&self) -> u32;
+    #[stable(feature = "metadata_ext2", since = "1.8.0")]
+    fn st_padding1(&self) -> u16;
 }
 
 #[stable(feature = "metadata_ext", since = "1.1.0")]
@@ -87,8 +89,8 @@ impl MetadataExt for Metadata {
                                           as *const raw::stat)
         }
     }
-    fn st_dev(&self) -> u64 {
-        self.as_inner().as_inner().st_dev as u64
+    fn st_dev(&self) -> u32 {
+        self.as_inner().as_inner().st_dev as u32
     }
     fn st_ino(&self) -> u64 {
         self.as_inner().as_inner().st_ino as u64
@@ -96,8 +98,8 @@ impl MetadataExt for Metadata {
     fn st_mode(&self) -> u32 {
         self.as_inner().as_inner().st_mode as u32
     }
-    fn st_nlink(&self) -> u64 {
-        self.as_inner().as_inner().st_nlink as u64
+    fn st_nlink(&self) -> u32 {
+        self.as_inner().as_inner().st_nlink as u32
     }
     fn st_uid(&self) -> u32 {
         self.as_inner().as_inner().st_uid as u32
@@ -129,11 +131,11 @@ impl MetadataExt for Metadata {
     fn st_ctime_nsec(&self) -> i64 {
         self.as_inner().as_inner().st_ctime_nsec as i64
     }
-    fn st_birthtime(&self) -> i64 {
-        self.as_inner().as_inner().st_birthtime as i64
+    fn st_qspare1(&self) -> i64 {
+        self.as_inner().as_inner().st_qspare1 as i64
     }
-    fn st_birthtime_nsec(&self) -> i64 {
-        self.as_inner().as_inner().st_birthtime_nsec as i64
+    fn st_qspare2(&self) -> i64 {
+        self.as_inner().as_inner().st_qspare2 as i64
     }
     fn st_blksize(&self) -> u64 {
         self.as_inner().as_inner().st_blksize as u64
@@ -150,5 +152,8 @@ impl MetadataExt for Metadata {
     fn st_lspare(&self) -> u32 {
         self.as_inner().as_inner().st_lspare as u32
     }
+    fn st_padding1(&self) -> u16 {
+        self.as_inner().as_inner().st_padding1 as u16
+    }
 }
 
