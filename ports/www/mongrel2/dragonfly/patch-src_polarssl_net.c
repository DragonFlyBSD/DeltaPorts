--- src/polarssl/net.c.orig	2011-06-22 19:25:12.000000000 +0300
+++ src/polarssl/net.c
@@ -59,7 +59,7 @@ static int wsa_init_done = 0;
 #include <netdb.h>
 #include <errno.h>
 
-#if defined(__FreeBSD__)
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #include <sys/endian.h>
 #elif defined(__APPLE__)
 #include <machine/endian.h>
