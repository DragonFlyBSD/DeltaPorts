--- src/loader/loader.c.orig	2015-10-17 15:07:35 +0200
+++ src/loader/loader.c
@@ -546,6 +546,24 @@
    return (*chip_id >= 0);
 }
 
+static char *
+devq_get_device_name_for_fd(int fd)
+{
+   size_t len;
+   char buf[0x40];
+   char *device_name = NULL;
+   DEVQ_SYMBOL(int, devq_device_get_name_from_fd,
+               (int fd, char *name, size_t *name_len));
+
+   len = sizeof(buf) - 1;
+   memset(buf, 0, sizeof(buf));
+   if (devq_device_get_name_from_fd(fd, buf, &len) == 0) {
+      if (len < sizeof(buf))
+         device_name = strdup(buf);
+   }
+   return device_name;
+}
+
 #endif
 
 #if !defined(__NOT_HAVE_DRM_H)
@@ -733,12 +751,9 @@
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
 
