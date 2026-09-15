--- glib/glib-unix.c.orig
+++ glib/glib-unix.c
@@ -55,6 +55,10 @@
-
+
 #if defined(__linux__) || defined(__DragonFly__)
 #include <sys/syscall.h>  /* for syscall and SYS_getdents64 */
+#endif
+
+#ifdef __DragonFly__
+#include <sys/param.h>    /* for MAXPATHLEN */
 #endif
-
+
 #ifdef HAVE_SYS_RESOURCE_H
@@ -967,7 +971,7 @@
   g_free (proc_path);
-
+
   return g_steal_pointer (&path);
-#elif defined (__FreeBSD__) || defined(__DragonFly__)
+#elif defined (__FreeBSD__)
   struct kinfo_file kf = {0};
-
+
   kf.kf_structsize = sizeof (kf);
@@ -982,7 +986,7 @@
     }
-
+
   return g_strdup (kf.kf_path);
-#elif defined (__APPLE__) || defined (__NetBSD__) || defined (__OpenBSD__)
+#elif defined (__APPLE__) || defined (__NetBSD__) || defined (__OpenBSD__) || defined (__DragonFly__)
   char file_path[MAXPATHLEN] = {0};
-
+
   if (fcntl (fd, F_GETPATH, file_path) < 0)
