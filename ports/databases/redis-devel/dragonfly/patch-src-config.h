--- redis-devel/redis-4.0.1/src/config.h	2017-09-22 20:32:36.607128000 +0300
+++ redis-devel.new/redis-4.0.1/src/config.h	2017-09-22 20:32:14.037121000 +0300
@@ -62,7 +62,7 @@
 #endif
 
 /* Test for backtrace() */
-#if defined(__APPLE__) || (defined(__linux__) && defined(__GLIBC__)) || defined(__FreeBSD__)
+#if defined(__APPLE__) || (defined(__linux__) && defined(__GLIBC__)) || defined(__FreeBSD__) || defined(__DragonFly__)
 #define HAVE_BACKTRACE 1
 #endif
 
@@ -76,7 +76,7 @@
 #define HAVE_EPOLL 1
 #endif
 
-#if (defined(__APPLE__) && defined(MAC_OS_X_VERSION_10_6)) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined (__NetBSD__)
+#if (defined(__APPLE__) && defined(MAC_OS_X_VERSION_10_6)) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined (__NetBSD__) || defined(__DragonFly__)
 #define HAVE_KQUEUE 1
 #endif
 
