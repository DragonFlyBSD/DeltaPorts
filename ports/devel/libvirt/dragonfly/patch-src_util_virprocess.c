--- src/util/virprocess.c.orig	2025-12-17 11:41:08 UTC
+++ src/util/virprocess.c
@@ -506,6 +506,22 @@ virProcessGetAffinity(pid_t pid)
 int virProcessSetAffinity(pid_t pid, virBitmap *map, bool quiet)
 {
     size_t i;
+#ifndef CPU_ALLOC
+    /* Legacy method uses a fixed size cpu mask, only allows up to 1024 cpus */
+    cpu_set_t mask;
+
+    CPU_ZERO(&mask);
+    for (i = 0; i < virBitmapSize(map); i++) {
+        if (virBitmapIsBitSet(map, i))
+            CPU_SET(i, &mask);
+    }
+
+    if (sched_setaffinity(pid, sizeof(mask), &mask) < 0) {
+        virReportSystemError(errno,
+                             _("cannot set CPU affinity on process %d"), pid);
+        return -1;
+    }
+#else
     int numcpus = 1024;
     size_t masklen;
     cpu_set_t *mask;
@@ -574,9 +590,18 @@ virProcessGetAffinity(pid_t pid)
         abort();
 
     CPU_ZERO_S(masklen, mask);
+#else
+    ncpus = 256; /* XXX */
+    masklen = sizeof(maskt);
+    CPU_ZERO(&maskt);
+# endif
 
-    if (sched_getaffinity(pid, masklen, mask) < 0) {
-        virReportSystemError(errno,
+# ifdef CPU_ALLOC
+     if (sched_getaffinity(pid, masklen, mask) < 0) {
+# else
+    if (sched_getaffinity(pid, masklen, &maskt) < 0) {
+#endif
+		virReportSystemError(errno,
                              _("cannot get CPU affinity of process %1$d"), pid);
         goto cleanup;
     }
@@ -584,12 +609,19 @@ virProcessGetAffinity(pid_t pid)
     ret = virBitmapNew(ncpus);
 
     for (i = 0; i < ncpus; i++) {
+#ifdef CPU_ALLOC
         if (CPU_ISSET_S(i, masklen, mask))
             ignore_value(virBitmapSetBit(ret, i));
+#else
+        if (CPU_ISSET(i, &maskt))
+            ignore_value(virBitmapSetBit(ret, i));
+# endif
     }
 
  cleanup:
+#ifdef CPU_ALLOC
     CPU_FREE(mask);
+#endif
 
     return ret;
 }
@@ -1173,7 +1205,7 @@ int virProcessGetStartTime(pid_t pid,
     }
     return 0;
 }
-#elif defined(__FreeBSD__) || defined(__FreeBSD_kernel__)
+#elif defined(__FreeBSD__) && ! defined __DragonFly__
 int virProcessGetStartTime(pid_t pid,
                            unsigned long long *timestamp)
 {
