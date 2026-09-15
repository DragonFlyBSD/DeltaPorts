--- src/polkit/polkitunixprocess.c.orig
+++ src/polkit/polkitunixprocess.c
@@ -30,6 +30,12 @@
 #ifdef HAVE_FREEBSD
 #include <sys/param.h>
 #include <sys/sysctl.h>
+#include <sys/user.h>
+#endif
+#ifdef HAVE_DRAGONFLY
+#include <sys/param.h>
+#include <sys/sysctl.h>
+#include <sys/kinfo.h>
 #include <sys/user.h>
 #endif
 #ifdef HAVE_NETBSD
@@ -204,7 +210,7 @@
 get_cgroupid_for_pidfd (gint     pidfd,
                         GError **error);
 
-#if defined(HAVE_FREEBSD) || defined(HAVE_NETBSD) || defined(HAVE_OPENBSD)
+#if defined(HAVE_FREEBSD) || defined(HAVE_NETBSD) || defined(HAVE_OPENBSD) || defined(HAVE_DRAGONFLY)
 static gboolean get_kinfo_proc (gint pid,
 #if defined(HAVE_NETBSD)
                                 struct kinfo_proc2 *p);
@@ -1205,7 +1211,7 @@
 }
 #endif
 
-#ifdef HAVE_FREEBSD
+#if defined(HAVE_FREEBSD) || defined(HAVE_DRAGONFLY)
 static gboolean
 get_kinfo_proc (pid_t pid, struct kinfo_proc *p)
 {
@@ -1263,7 +1269,7 @@
                         GError **error)
 {
   guint64 start_time;
-#if !defined(HAVE_FREEBSD) && !defined(HAVE_NETBSD) && !defined(HAVE_OPENBSD)
+#if !defined(HAVE_FREEBSD) && !defined(HAVE_NETBSD) && !defined(HAVE_OPENBSD) && !defined(HAVE_DRAGONFLY)
   gchar *filename;
   gchar *contents;
   size_t length;
@@ -1355,8 +1361,10 @@
       goto out;
     }
 
-#ifdef HAVE_FREEBSD
+#if defined(HAVE_FREEBSD)
   start_time = (guint64) p.ki_start.tv_sec;
+#elif defined(HAVE_DRAGONFLY)
+  start_time = (guint64) p.kp_start.tv_sec;
 #else
   start_time = (guint64) p.p_ustart_sec;
 #endif
@@ -1810,7 +1818,7 @@
   gchar *contents;
   gchar **lines;
   guint64 start_time;
-#if defined(HAVE_FREEBSD) || defined(HAVE_OPENBSD)
+#if defined(HAVE_FREEBSD) || defined(HAVE_OPENBSD) || defined(HAVE_DRAGONFLY)
   struct kinfo_proc p;
 #elif defined(HAVE_NETBSD)
   struct kinfo_proc2 p;
@@ -1837,7 +1845,7 @@
       goto out;
     }
 
-#if defined(HAVE_FREEBSD) || defined(HAVE_NETBSD) || defined(HAVE_OPENBSD)
+#if defined(HAVE_FREEBSD) || defined(HAVE_NETBSD) || defined(HAVE_OPENBSD) || defined(HAVE_DRAGONFLY)
   if (get_kinfo_proc (pid, &p) == 0)
     {
       g_set_error (error,
@@ -1852,6 +1860,9 @@
 #if defined(HAVE_FREEBSD)
   result = p.ki_uid;
   start_time = (guint64) p.ki_start.tv_sec;
+#elif defined(HAVE_DRAGONFLY)
+  result = p.kp_uid;
+  start_time = (guint64) p.kp_start.tv_sec;
 #else
   result = p.p_uid;
   start_time = (guint64) p.p_ustart_sec;
