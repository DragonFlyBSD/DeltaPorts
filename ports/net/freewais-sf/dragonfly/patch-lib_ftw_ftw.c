--- lib/ftw/ftw.c.orig	2000-06-08 06:33:04.000000000 +0000
+++ lib/ftw/ftw.c
@@ -20,6 +20,7 @@
  */
 
 #include <stdio.h>
+#include <limits.h>
 #include "cdialect.h"
 #include <sys/stat.h>
 #define FTW_F		0	/* A normal file			*/
@@ -71,7 +72,7 @@ ftw(directory, funcptr, depth)
 
     /* Get ready to hold the full paths. */
     i = strlen(directory);
-    fullpath = (char *)malloc(i + 1 + MAXNAMLEN + 1);
+    fullpath = (char *)malloc(i + 1 + NAME_MAX + 1);
     if (fullpath == NULL) {
 	closedir(dirp);
 	return -1;
