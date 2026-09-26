--- src/backends/meta-screen-cast-stream-src.c.orig
+++ src/backends/meta-screen-cast-stream-src.c
@@ -2301,7 +2301,9 @@
     }
   else
     {
+#ifndef __DragonFly__
       unsigned int seals;
+#endif
 
       priv->uses_dma_bufs = FALSE;
 
@@ -2319,8 +2321,26 @@
       /* Fallback to a memfd buffer */
       spa_data->type = SPA_DATA_MemFd;
       spa_data->flags = SPA_DATA_FLAG_READABLE | SPA_DATA_FLAG_MAPPABLE;
+#ifdef __DragonFly__
+      {
+        /* DragonFly has no memfd_create or file sealing; back the buffer
+         * with an anonymous (named then unlinked) POSIX shm object, which
+         * is mappable and passable over SCM_RIGHTS like a memfd. */
+        char shm_name[64];
+        static int shm_serial;
+
+        g_snprintf (shm_name, sizeof (shm_name),
+                    "/mutter-screen-cast-%d-%d",
+                    (int) getpid (), shm_serial++);
+        spa_data->fd = shm_open (shm_name,
+                                 O_CREAT | O_EXCL | O_RDWR | O_CLOEXEC, 0600);
+        if (spa_data->fd != -1)
+          shm_unlink (shm_name);
+      }
+#else
       spa_data->fd = memfd_create ("mutter-screen-cast-memfd",
                                    MFD_CLOEXEC | MFD_ALLOW_SEALING);
+#endif
       if (spa_data->fd == -1)
         {
           g_critical ("Can't create memfd: %m");
@@ -2338,9 +2358,11 @@
           return;
         }
 
+#ifndef __DragonFly__
       seals = F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
       if (fcntl (spa_data->fd, F_ADD_SEALS, seals) == -1)
         g_warning ("Failed to add seals: %m");
+#endif
 
       spa_data->data = mmap (NULL,
                              spa_data->maxsize,
