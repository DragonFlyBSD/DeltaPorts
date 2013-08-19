--- lib/ftw/scandir.c.intermediate	2013-08-19 21:04:44.186417000 +0000
+++ lib/ftw/scandir.c
@@ -13,7 +13,7 @@
 
 #include "config.h"
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #define HAS_SCANDIR 1
 #endif
 
