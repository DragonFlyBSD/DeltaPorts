--- src/mem/align.h.orig	2011-06-22 19:25:12.000000000 +0300
+++ src/mem/align.h
@@ -18,6 +18,7 @@
 /*
  *	a type with the most strict alignment requirements
  */
+#ifndef __DragonFly__
 union max_align
 {
 	char   c;
@@ -31,6 +32,7 @@ union max_align
 };
 
 typedef union max_align max_align_t;
+#endif
 
 #endif
 
