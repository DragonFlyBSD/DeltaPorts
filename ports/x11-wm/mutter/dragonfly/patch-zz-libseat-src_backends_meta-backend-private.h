--- src/backends/meta-backend-private.h.orig
+++ src/backends/meta-backend-private.h
@@ -128,7 +128,7 @@
 META_EXPORT_TEST
 MetaColorManager * meta_backend_get_color_manager (MetaBackend *backend);
 
-#ifdef HAVE_LOGIND
+#ifdef HAVE_LAUNCHER
 META_EXPORT_TEST
 MetaLauncher * meta_backend_get_launcher (MetaBackend *backend);
 #endif
