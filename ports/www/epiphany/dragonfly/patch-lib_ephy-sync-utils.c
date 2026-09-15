--- lib/ephy-sync-utils.c.orig
+++ lib/ephy-sync-utils.c
@@ -33,3 +33,3 @@
 #if defined(__linux__)
 #include <sys/random.h>
-#elif defined(__FreeBSD__) || defined(__OpenBSD__)
+#elif defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__DragonFly__)
@@ -185 +185 @@
-#ifdef __OpenBSD__
+#if defined(__OpenBSD__) || defined(__DragonFly__)
