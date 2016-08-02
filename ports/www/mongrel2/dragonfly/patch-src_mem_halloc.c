Typo in OpenBSD case?

--- src/mem/halloc.c.orig	2011-06-22 19:25:12.000000000 +0300
+++ src/mem/halloc.c
@@ -12,7 +12,7 @@
  *	http://www.opensource.org/licenses/bsd-license.php
  */
 
-#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__OpenBSD)
+#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__DragonFly__)
 #include <stdlib.h>
 #else
 #include <malloc.h>  /* realloc */
