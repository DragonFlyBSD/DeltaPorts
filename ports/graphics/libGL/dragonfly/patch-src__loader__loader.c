--- src/loader/loader.c.fbsd	2016-01-31 20:22:25.921378000 +0100
+++ src/loader/loader.c	2016-01-31 20:25:16.963592000 +0100
@@ -724,6 +724,23 @@
 #endif
 
 
+#if HAVE_LIBDEVQ
+static char *
+devq_get_device_name_for_fd(int fd)
+{
+   char buf[0x40];
+   size_t len = sizeof(buf);
+
+   DEVQ_SYMBOL(int, devq_device_get_devpath_from_fd,
+               (int fd, char *path, size_t *path_len));
+
+   if (devq_device_get_devpath_from_fd(fd, buf, &len) != 0)
+      return NULL;
+
+   return strdup(buf);
+}
+#endif
+
 char *
 loader_get_device_name_for_fd(int fd)
 {
@@ -738,12 +755,9 @@
       return result;
 #endif
 #if HAVE_LIBDEVQ
-#if 0
-/* XXX implement this function in libdevq */
-   if ((result = devq_device_get_name_for_fd(fd)))
+   if ((result = devq_get_device_name_for_fd(fd)))
       return result;
 #endif
-#endif
    return result;
 }
 
