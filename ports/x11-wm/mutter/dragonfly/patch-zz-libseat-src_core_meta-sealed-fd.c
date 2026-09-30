--- src/core/meta-sealed-fd.c.orig
+++ src/core/meta-sealed-fd.c
@@ -30,7 +30,9 @@
 #include <sys/mman.h>
 #include <unistd.h>
 
+#ifdef F_ADD_SEALS
 #define REQUIRED_SEALS (F_SEAL_GROW | F_SEAL_WRITE | F_SEAL_SHRINK)
+#endif
 
 struct _MetaSealedFd
 {
@@ -78,6 +80,12 @@
 
   g_return_val_if_fail (fd != -1, NULL);
 
+#ifndef F_ADD_SEALS
+  /* No file sealing on this platform (e.g. DragonFly); accept the fd
+   * as-is. Sealing is defense in depth, not a functional requirement. */
+  (void) saved_errno;
+  (void) seals;
+#else
   seals = fcntl (fd, F_GET_SEALS);
   if (seals == -1)
    {
@@ -104,6 +112,7 @@
                    "fcntl F_ADD_SEALS: %s", g_strerror (saved_errno));
       return NULL;
     }
+#endif
 
   sealed_fd = g_object_new (META_TYPE_SEALED_FD, NULL);
   sealed_fd->fd = g_steal_fd (&fd);
