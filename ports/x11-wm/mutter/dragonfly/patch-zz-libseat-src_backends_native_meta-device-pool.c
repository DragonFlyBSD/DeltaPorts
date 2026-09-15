--- src/backends/native/meta-device-pool.c.orig
+++ src/backends/native/meta-device-pool.c
@@ -22,7 +22,6 @@
 #include <fcntl.h>
 #include <gio/gunixfdlist.h>
 #include <sys/stat.h>
-#include <sys/sysmacros.h>
 #include <sys/types.h>
 
 #include "backends/meta-launcher.h"
@@ -42,6 +41,9 @@
   int major;
   int minor;
   int fd;
+#ifdef HAVE_LIBSEAT
+  int device_id;
+#endif
   MetaDeviceFileFlags flags;
   uint32_t tags[META_DEVICE_FILE_N_TAGS];
 };
@@ -53,6 +55,9 @@
   MetaBackend *backend;
 
   MetaDBusLogin1Session *session_proxy;
+#ifdef HAVE_LIBSEAT
+  MetaLauncher *launcher;
+#endif
 
   GMutex mutex;
 
@@ -250,6 +255,35 @@
                   "Opening and taking control of device file '%s'",
                   path);
 
+#ifdef HAVE_LIBSEAT
+      int device_id;
+
+      if (!pool->launcher)
+        {
+          g_set_error (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
+                       "Can't take control without a libseat seat");
+          return NULL;
+        }
+
+      if (!get_device_info_from_path (path, &major, &minor))
+        {
+          g_set_error (error,
+                       G_IO_ERROR,
+                       G_IO_ERROR_NOT_FOUND,
+                       "Could not get device info for path %s: %m", path);
+          return NULL;
+        }
+
+      device_id = meta_launcher_open_device (pool->launcher, path, &fd, error);
+      if (device_id < 0)
+        return NULL;
+
+      file = meta_device_file_new (pool, path, major, minor, fd, flags);
+      file->device_id = device_id;
+      pool->files = g_list_prepend (pool->files, file);
+
+      return file;
+#else
       if (!pool->session_proxy)
         {
           g_set_error (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
@@ -268,6 +302,7 @@
 
       if (!take_device (pool->session_proxy, major, minor, &fd, NULL, error))
         return NULL;
+#endif
     }
   else
     {
@@ -322,6 +357,13 @@
 
   if (file->flags & META_DEVICE_FILE_FLAG_TAKE_CONTROL)
     {
+#ifdef HAVE_LIBSEAT
+      meta_topic (META_DEBUG_BACKEND,
+                  "Releasing control of and closing device file '%s'",
+                  file->path);
+
+      meta_launcher_close_device (pool->launcher, file->device_id);
+#else
       MetaDBusLogin1Session *session_proxy;
 
       meta_topic (META_DEBUG_BACKEND,
@@ -339,6 +381,7 @@
                      file->major, file->minor,
                      error->message);
         }
+#endif
     }
   else
     {
@@ -364,7 +407,12 @@
 
   launcher = meta_backend_get_launcher (pool->backend);
   if (launcher)
-    pool->session_proxy = meta_launcher_get_session_proxy (launcher);
+    {
+      pool->session_proxy = meta_launcher_get_session_proxy (launcher);
+#ifdef HAVE_LIBSEAT
+      pool->launcher = launcher;
+#endif
+    }
 
   return pool;
 }
