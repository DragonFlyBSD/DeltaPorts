--- deps/uv/src/unix/stream.c.orig	2016-05-22 22:43:49 UTC
+++ deps/uv/src/unix/stream.c
@@ -961,6 +961,14 @@ uv_handle_type uv__handle_type(int fd) {
     return UV_UNKNOWN_HANDLE;

   if (type == SOCK_STREAM) {
+#if defined(_AIX) || defined(__DragonFly__)
+    /* on AIX/DragonFly the getsockname call returns an empty sa structure
+     * for sockets of type AF_UNIX.  For all other types it will
+     * return a properly filled in structure.
+     */
+    if (len == 0)
+      return UV_NAMED_PIPE;
+#endif
     switch (ss.ss_family) {
       case AF_UNIX:
         return UV_NAMED_PIPE;
