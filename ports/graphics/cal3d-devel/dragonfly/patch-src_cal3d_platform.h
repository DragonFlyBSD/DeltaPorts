--- src/cal3d/platform.h.orig	2006-03-02 00:55:35.000000000 +0200
+++ src/cal3d/platform.h
@@ -77,6 +77,9 @@ typedef int intptr_t;
 // standard includes
 #include <stdlib.h>
 #include <math.h>
+#ifdef __DragonFly__
+#include <string.h>
+#endif
 
 // debug includes
 #include <assert.h>
