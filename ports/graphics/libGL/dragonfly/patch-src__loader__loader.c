--- src/loader/loader.c.fbsd	2016-01-31 20:22:25.921378000 +0100
+++ src/loader/loader.c	2016-01-31 20:25:16.963592000 +0100
@@ -531,25 +531,6 @@
 #define DEVQ_SYMBOL(ret, name, args) \
    ret (*name) args = asserted_dlsym(devq_dlopen_handle(), #name);
 
-static int
-devq_get_pci_id_from_fd(int fd, int *vendor_id, int *chip_id)
-{
-   int ret;
-   DEVQ_SYMBOL(int, devq_device_get_pciid_from_fd,
-               (int fd, int *vendor_id, int *chip_id));
-
-   *chip_id = -1;
-
-   ret = devq_device_get_pciid_from_fd(fd, vendor_id, chip_id);
-   if (ret < 0) {
-      log_(_LOADER_WARNING, "MESA-LOADER: could not get PCI ID\n");
-      goto out;
-   }
-
-out:
-   return (*chip_id >= 0);
-}
-
 #endif
 
 
@@ -636,10 +617,6 @@
    if (sysfs_get_pci_id_for_fd(fd, vendor_id, chip_id))
       return 1;
 #endif
-#if HAVE_LIBDEVQ
-   if (devq_get_pci_id_from_fd(fd, vendor_id, chip_id))
-      return 1;
-#endif
 #if HAVE_LIBDRM
    if (drm_get_pci_id_for_fd(fd, vendor_id, chip_id))
       return 1;
@@ -724,6 +701,23 @@
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
@@ -738,12 +732,9 @@
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
 
