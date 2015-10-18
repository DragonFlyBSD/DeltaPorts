--- include/libdevq.h.orig	2015-10-17 14:25:05 +0200
+++ include/libdevq.h
@@ -41,6 +41,8 @@
 
 int	devq_device_get_devpath_from_fd(int fd,
 	    char *path, size_t *path_len);
+int	devq_device_get_name_from_fd(int fd,
+	    char *name, size_t *name_len);
 int	devq_device_get_pciid_from_fd(int fd,
 	    int *vendor_id, int *device_id);
 
