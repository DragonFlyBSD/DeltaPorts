--- vendor/llama.cpp/vendor/sheredom/subprocess.h.orig
+++ vendor/llama.cpp/vendor/sheredom/subprocess.h
@@ -298,7 +298,7 @@
    spelling: the child chdir()s before exec, and a close-on-exec pipe carries
    exec's errno back. Define this yourself to force either implementation. */
 #if !defined(SUBPROCESS_SPAWN_VIA_FORK)
-#if defined(_AIX) || defined(__OpenBSD__) ||                                  \
+#if defined(_AIX) || defined(__OpenBSD__) || defined(__DragonFly__) ||           \
     (defined(__NetBSD__) && (__NetBSD_Version__ < 1000000000))
 #define SUBPROCESS_SPAWN_VIA_FORK 1
 #else
