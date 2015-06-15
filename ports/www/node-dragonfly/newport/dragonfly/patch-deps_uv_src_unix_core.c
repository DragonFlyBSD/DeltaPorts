--- deps/uv/src/unix/core.c.orig	2015-06-15 00:52:55.239907000 +0200
+++ deps/uv/src/unix/core.c	2015-06-15 01:02:24.209056000 +0200
@@ -54,13 +54,13 @@
 # include <sys/ioctl.h>
 #endif
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 # include <sys/sysctl.h>
 # include <sys/filio.h>
 # include <sys/ioctl.h>
 # include <sys/wait.h>
 # define UV__O_CLOEXEC O_CLOEXEC
-# if __FreeBSD__ >= 10
+# if defined(__FreeBSD__) && __FreeBSD__ >= 10
 #  define uv__accept4 accept4
 #  define UV__SOCK_NONBLOCK SOCK_NONBLOCK
 #  define UV__SOCK_CLOEXEC  SOCK_CLOEXEC
@@ -473,7 +473,7 @@
 
 
 #if defined(__linux__) || defined(__FreeBSD__) || defined(__APPLE__) || \
-    defined(_AIX)
+    defined(_AIX) || defined(__DragonFly__)
 
 int uv__nonblock(int fd, int set) {
   int r;
@@ -502,7 +502,8 @@
   return 0;
 }
 
-#else /* !(defined(__linux__) || defined(__FreeBSD__) || defined(__APPLE__)) */
+#else /* !(defined(__linux__) || defined(__FreeBSD__) || defined(__APPLE__) || \
+	   defined(_AIX) || defined(__DragonFly__)) */
 
 int uv__nonblock(int fd, int set) {
   int flags;
@@ -565,7 +566,8 @@
   return 0;
 }
 
-#endif /* defined(__linux__) || defined(__FreeBSD__) || defined(__APPLE__) */
+#endif /* defined(__linux__) || defined(__FreeBSD__) || defined(__APPLE__) || \
+	  defined(_AIX) || defined(__DragonFly__) */
 
 
 /* This function is not execve-safe, there is a race window
@@ -899,7 +901,8 @@
   int err;
   int fd;
 
-#if defined(__linux__) || (defined(__FreeBSD__) && __FreeBSD__ >= 9)
+#if defined(__linux__) || (defined(__FreeBSD__) && __FreeBSD__ >= 9) || \
+    defined(__DragonFly__)
   static int no_cloexec;
 
   if (!no_cloexec) {
