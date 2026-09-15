--- src/nautilus-file-utilities.c.orig	2026-07-04 23:11:58 UTC
+++ src/nautilus-file-utilities.c
@@ -41,7 +41,9 @@
 #include <gio/gio.h>
 #include <unistd.h>
 #include <stdlib.h>
+#ifdef __linux__
 #include <sys/vfs.h>
+#endif
 
 #define NAUTILUS_USER_DIRECTORY_NAME "nautilus"
 #define DEFAULT_NAUTILUS_DIRECTORY_MODE (0755)
@@ -1160,7 +1162,7 @@ gboolean
 nautilus_location_is_autofs_mountpoint (GFile *location)
 {
     g_return_val_if_fail (G_IS_FILE (location), FALSE);
-
+#ifdef __linux__
     g_autofree char *path = g_file_get_path (location);
     struct statfs buf;
     gint fd;
@@ -1185,4 +1187,8 @@ nautilus_location_is_autofs_mountpoint (GFile *locatio
     close (fd);
 
     return (buf.f_type == AUTOFS_SUPER_MAGIC);
+#else
+    /* autofs is Linux-specific; there is no autofs mount to avoid here. */
+    return FALSE;
+#endif
 }
