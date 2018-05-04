--- channels/chan_oss.c.orig	2018-05-01 20:12:26 UTC
+++ channels/chan_oss.c
@@ -51,7 +51,7 @@
 
 #ifdef __linux
 #include <linux/soundcard.h>
-#elif defined(__FreeBSD__) || defined(__CYGWIN__) || defined(__GLIBC__) || defined(__sun)
+#elif defined(__FreeBSD__) || defined(__CYGWIN__) || defined(__GLIBC__) || defined(__sun) || defined(__DragonFly__)
 #include <sys/soundcard.h>
 #else
 #include <soundcard.h>
