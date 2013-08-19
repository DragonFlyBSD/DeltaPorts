--- lib/ftw/alphasort.c.intermediate	2013-08-19 21:04:44.176417000 +0000
+++ lib/ftw/alphasort.c
@@ -13,7 +13,7 @@
 
 #include "config.h"
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #define HAS_ALPHASORT 1
 #endif
 
