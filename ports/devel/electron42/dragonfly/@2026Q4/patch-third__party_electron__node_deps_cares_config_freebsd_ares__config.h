--- third_party/electron_node/deps/cares/config/freebsd/ares_config.h.orig
+++ third_party/electron_node/deps/cares/config/freebsd/ares_config.h
@@ -173,7 +173,9 @@
 #define HAVE_LONGLONG 1
 
 /* Define to 1 if you have the <malloc.h> header file. */
+#ifndef __DragonFly__
 #define HAVE_MALLOC_H 1
+#endif
 
 /* Define to 1 if you have the <memory.h> header file. */
 #define HAVE_MEMORY_H 1
