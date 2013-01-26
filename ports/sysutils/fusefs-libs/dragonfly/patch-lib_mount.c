--- lib/mount.c.orig	2012-09-04 12:17:46.000000000 +0200
+++ lib/mount.c	2013-01-26 20:02:10.135573000 +0100
@@ -26,8 +26,7 @@
 #include <sys/wait.h>
 #include <sys/mount.h>
 
-#ifdef __NetBSD__
-#include <perfuse.h>
+#ifdef __DragonFly__
 
 #define MS_RDONLY 	MNT_RDONLY
 #define MS_NOSUID 	MNT_NOSUID
