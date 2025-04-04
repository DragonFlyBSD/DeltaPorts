diff --git tools/gn/src/base/files/file_posix.cc tools/gn/src/base/files/file_posix.cc
index e4fccce0b7dc..a43555b7efa9 100644
--- tools/gn/src/base/files/file_posix.cc
+++ tools/gn/src/base/files/file_posix.cc
@@ -371,7 +371,7 @@ void File::DoInitialize(const FilePath& path, uint32_t flags) {
 bool File::Flush() {
   DCHECK(IsValid());
 
-#if defined(OS_LINUX) || defined(OS_BSD)
+#if defined(OS_LINUX) || defined(OS_BSD) && !defined(OS_DRAGONFLY)
   return !HANDLE_EINTR(fdatasync(file_.get()));
 #else
   return !HANDLE_EINTR(fsync(file_.get()));
