--- src/wayland/meta-xwayland.c.orig
+++ src/wayland/meta-xwayland.c
@@ -602,16 +602,23 @@
 {
   g_autofd int abstract_fd = -1, unix_fd = -1;
 
+  unix_fd = bind_to_unix_socket (display_index, error);
+  if (unix_fd < 0)
+    return FALSE;
+
   if (abstract_fd_out)
     {
-      abstract_fd = bind_to_abstract_socket (display_index, error);
+      /* Abstract unix sockets are Linux-only; hand out a duplicate of
+       * the filesystem socket instead. */
+      abstract_fd = fcntl (unix_fd, F_DUPFD_CLOEXEC, 3);
       if (abstract_fd < 0)
-        return FALSE;
+        {
+          g_set_error (error, G_IO_ERROR, g_io_error_from_errno (errno),
+                       "Failed to duplicate X11 socket: %s",
+                       g_strerror (errno));
+          return FALSE;
+        }
     }
-
-  unix_fd = bind_to_unix_socket (display_index, error);
-  if (unix_fd < 0)
-    return FALSE;
 
   if (abstract_fd_out)
     *abstract_fd_out = g_steal_fd (&abstract_fd);
