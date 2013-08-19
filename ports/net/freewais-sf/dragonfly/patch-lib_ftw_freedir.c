--- lib/ftw/freedir.c.orig	2000-06-08 06:33:04.000000000 +0000
+++ lib/ftw/freedir.c
@@ -13,6 +13,8 @@
 
 
 /* free list malloc'd by scandir */
+#include <stdlib.h>
+
 void
 freedir(list)
   char **list;
