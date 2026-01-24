--- src/file_reader.cpp.orig	2026-01-05 15:01:26 UTC
+++ src/file_reader.cpp
@@ -143,7 +143,7 @@ class MMapException : std::exception {};
 char*
 mmapReadOnly(int fd, offset_type offset, size_type size)
 {
-#if defined(__APPLE__) || defined(__OpenBSD__) || defined(__HAIKU__)
+#if defined(__APPLE__) || defined(__OpenBSD__) || defined(__DragonFly__)
   const auto MAP_FLAGS = MAP_PRIVATE;
 #elif defined(__FreeBSD__)
   const auto MAP_FLAGS = MAP_PRIVATE|MAP_PREFAULT_READ;
