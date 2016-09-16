--- lib/mount_bsd.c.intermediate	2016-09-16 08:38:40 UTC
+++ lib/mount_bsd.c
@@ -207,7 +207,12 @@ void fuse_kern_unmount(const char *mount
 /* Check if kernel is doing init in background */
 static int init_backgrounded(void)
 {
+#ifdef __DragonFly__
+	unsigned ibg;
+	size_t len;
+#else
 	unsigned ibg, len;
+#endif
 
 	len = sizeof(ibg);
 
