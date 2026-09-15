--- src/backends/meta-launcher.h.orig
+++ src/backends/meta-launcher.h
@@ -48,3 +48,16 @@
 MetaDBusLogin1Session * meta_launcher_get_session_proxy (MetaLauncher *launcher);
 
 MetaBackend * meta_launcher_get_backend (MetaLauncher *launcher);
+
+#ifdef HAVE_LIBSEAT
+/* Device access through the libseat seat; used by MetaDevicePool in
+ * place of the login1 TakeDevice/ReleaseDevice calls. Returns the
+ * libseat device id, or -1 on error. */
+int meta_launcher_open_device (MetaLauncher  *launcher,
+                               const char    *path,
+                               int           *out_fd,
+                               GError       **error);
+
+void meta_launcher_close_device (MetaLauncher *launcher,
+                                 int           device_id);
+#endif
