--- lib/Support/Unix/Path.inc.orig	2019-01-18 21:04:59 UTC
+++ lib/Support/Unix/Path.inc
@@ -56,6 +56,7 @@
 
 #include <sys/types.h>
 #if !defined(__APPLE__) && !defined(__OpenBSD__) && !defined(__FreeBSD__) &&   \
+    !defined(__DragonFly__) &&
     !defined(__linux__) && !defined(__FreeBSD_kernel__)
 #include <sys/statvfs.h>
 #define STATVFS statvfs
