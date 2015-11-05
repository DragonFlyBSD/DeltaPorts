--- channels/chan_oss.c.orig	2015-11-04 23:36:40.263544000 +0100
+++ channels/chan_oss.c
@@ -53,7 +53,7 @@
 
 #ifdef __linux
 #include <linux/soundcard.h>
-#elif defined(__FreeBSD__) || defined(__CYGWIN__) || defined(__GLIBC__) || defined(__sun)
+#elif defined(__FreeBSD__) || defined(__CYGWIN__) || defined(__GLIBC__) || defined(__sun) || defined(__DragonFly__)
 #include <sys/soundcard.h>
 #else
 #include <soundcard.h>
