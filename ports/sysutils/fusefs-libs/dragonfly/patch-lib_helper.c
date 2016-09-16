--- lib/helper.c.intermediate	2016-09-16 08:13:42 UTC
+++ lib/helper.c
@@ -382,7 +382,7 @@ int fuse_version(void)
 
 #include "fuse_compat.h"
 
-#if !defined(__FreeBSD__) && !defined(__NetBSD__)
+#if !defined(__FreeBSD__) && !defined(__NetBSD__) && !defined(__DragonFly__)
 
 struct fuse *fuse_setup_compat22(int argc, char *argv[],
 				 const struct fuse_operations_compat22 *op,
