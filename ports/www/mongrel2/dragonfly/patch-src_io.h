--- src/io.h.orig	2011-06-22 19:25:12.000000000 +0300
+++ src/io.h
@@ -11,7 +11,7 @@
 #include <polarssl/ssl.h>
 #include <polarssl/havege.h>
 
-#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__NetBSD__) || defined(__OpenBSD__)
+#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__NetBSD__) || defined(__OpenBSD__) || defined(__DragonFly__)
 #include "bsd_specific.h"
 #else
 #include <sys/sendfile.h>
@@ -94,7 +94,7 @@ int IOBuf_stream_file(IOBuf *buf, int fd
 
 #define IOBuf_fd(I) ((I)->fd)
 
-#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__NetBSD__) || defined(__OpenBSD__)
+#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__NetBSD__) || defined(__OpenBSD__) || defined(__DragonFly__)
 #define IOBuf_sendfile bsd_sendfile
 #else
 #define IOBuf_sendfile sendfile
