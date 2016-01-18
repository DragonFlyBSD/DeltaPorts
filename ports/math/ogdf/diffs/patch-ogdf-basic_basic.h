--- ogdf/basic/basic.h	2016-01-19 00:30:17.346430000 +0100
+++ ogdf/basic/basic.h	2016-01-19 00:30:03.416148000 +0100
@@ -92,6 +92,10 @@
 #define OGDF_SYSTEM_FREEBSD
 #endif
 
+#if defined(__DragonFly__)
+#define OGDF_SYSTEM_FREEBSD
+#endif
+
 #if defined(USE_COIN) || defined(OGDF_OWN_LPSOLVER)
 #define OGDF_LP_SOLVER
 #endif
