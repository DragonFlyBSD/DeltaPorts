--- src/libstd/sys/unix/ext/fs.rs.orig	2016-03-18 13:54:58 UTC
+++ src/libstd/sys/unix/ext/fs.rs
@@ -115,13 +115,13 @@ impl OpenOptionsExt for OpenOptions {
 #[stable(feature = "metadata_ext", since = "1.1.0")]
 pub trait MetadataExt {
     #[stable(feature = "metadata_ext", since = "1.1.0")]
-    fn dev(&self) -> u64;
+    fn dev(&self) -> u32;
     #[stable(feature = "metadata_ext", since = "1.1.0")]
     fn ino(&self) -> u64;
     #[stable(feature = "metadata_ext", since = "1.1.0")]
     fn mode(&self) -> u32;
     #[stable(feature = "metadata_ext", since = "1.1.0")]
-    fn nlink(&self) -> u64;
+    fn nlink(&self) -> u32;
     #[stable(feature = "metadata_ext", since = "1.1.0")]
     fn uid(&self) -> u32;
     #[stable(feature = "metadata_ext", since = "1.1.0")]
@@ -150,10 +150,10 @@ pub trait MetadataExt {
 
 #[stable(feature = "metadata_ext", since = "1.1.0")]
 impl MetadataExt for fs::Metadata {
-    fn dev(&self) -> u64 { self.st_dev() }
+    fn dev(&self) -> u32 { self.st_dev() }
     fn ino(&self) -> u64 { self.st_ino() }
     fn mode(&self) -> u32 { self.st_mode() }
-    fn nlink(&self) -> u64 { self.st_nlink() }
+    fn nlink(&self) -> u32 { self.st_nlink() }
     fn uid(&self) -> u32 { self.st_uid() }
     fn gid(&self) -> u32 { self.st_gid() }
     fn rdev(&self) -> u64 { self.st_rdev() }
