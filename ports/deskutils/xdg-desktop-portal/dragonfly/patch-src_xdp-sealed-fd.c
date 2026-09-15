--- src/xdp-sealed-fd.c.orig
+++ src/xdp-sealed-fd.c
@@ -34,6 +34,36 @@
 
 #define REQUIRED_SEALS (F_SEAL_GROW | F_SEAL_WRITE | F_SEAL_SHRINK)
 
+#ifdef __DragonFly__
+/* DragonFly has neither memfd_create nor file sealing. Back the "sealed" fd
+ * with an anonymous POSIX shared-memory object: create it named, then unlink
+ * the name immediately so nothing persists and the fd stays valid. The
+ * immutability that sealing would enforce is instead obtained by
+ * copy-on-receipt -- an fd coming from a less-trusted peer is copied into a
+ * fresh private object the peer can no longer touch (see
+ * xdp_sealed_fd_new_take_memfd). */
+static int
+create_anon_shm (GError **error)
+{
+  static gint counter = 0;
+  char name[64];
+  int fd;
+
+  g_snprintf (name, sizeof name, "/xdp-sealed-%d-%d",
+              (int) getpid (), g_atomic_int_add (&counter, 1));
+  fd = shm_open (name, O_RDWR | O_CREAT | O_EXCL, 0600);
+  if (fd == -1)
+    {
+      int saved_errno = errno;
+      g_set_error (error, G_IO_ERROR, g_io_error_from_errno (saved_errno),
+                   "shm_open: %s", g_strerror (saved_errno));
+      return -1;
+    }
+  shm_unlink (name);
+  return fd;
+}
+#endif
+
 struct _XdpSealedFd
 {
   GObject parent_instance;
@@ -73,6 +103,22 @@
 xdp_sealed_fd_new_take_memfd (int      memfd,
                               GError **error)
 {
+#ifdef __DragonFly__
+  /* Copy-on-receipt: snapshot the caller's fd into a private object it can no
+   * longer alter, giving the same guarantee that F_SEAL_WRITE would. */
+  g_autofd int in_fd = g_steal_fd (&memfd);
+  g_autoptr(GMappedFile) mapped = NULL;
+  g_autoptr(GBytes) bytes = NULL;
+
+  g_return_val_if_fail (in_fd != -1, NULL);
+
+  mapped = g_mapped_file_new_from_fd (in_fd, FALSE, error);
+  if (mapped == NULL)
+    return NULL;
+
+  bytes = g_mapped_file_get_bytes (mapped);
+  return xdp_sealed_fd_new_from_bytes (bytes, error);
+#else
   g_autoptr(XdpSealedFd) sealed_fd = NULL;
   g_autofd int fd = g_steal_fd (&memfd);
   int saved_errno = -1;
@@ -111,6 +157,7 @@
   sealed_fd->fd = g_steal_fd (&fd);
 
   return g_steal_pointer (&sealed_fd);
+#endif
 }
 
 XdpSealedFd *
@@ -123,10 +170,17 @@
   gconstpointer bytes_data;
   gpointer shm;
   gsize bytes_len;
+#ifndef __DragonFly__
   int saved_errno = -1;
+#endif
 
   g_return_val_if_fail (bytes != NULL, NULL);
 
+#ifdef __DragonFly__
+  fd = create_anon_shm (error);
+  if (fd == -1)
+    return NULL;
+#else
   fd = memfd_create ("xdp-sealed-fd", MFD_ALLOW_SEALING);
   if (fd == -1)
     {
@@ -139,6 +193,7 @@
                    "memfd_create: %s", g_strerror (saved_errno));
       return NULL;
     }
+#endif
 
   bytes_data = g_bytes_get_data (bytes, &bytes_len);
 
@@ -181,6 +236,7 @@
       return NULL;
     }
 
+#ifndef __DragonFly__
   if (fcntl (fd, F_ADD_SEALS, REQUIRED_SEALS) == -1)
     {
       saved_errno = errno;
@@ -190,6 +246,7 @@
                    "fcntl F_ADD_SEALS: %s", g_strerror (saved_errno));
       return NULL;
     }
+#endif
 
   sealed_fd = g_object_new (XDP_TYPE_SEALED_FD, NULL);
   sealed_fd->fd = g_steal_fd (&fd);
