--- lib/cutil.c.intermediate	2013-08-19 21:04:44.176417000 +0000
+++ lib/cutil.c
@@ -788,7 +788,7 @@ beFriendly()
 }
 
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #define HAS_VPRINTF 1
 #endif
 
