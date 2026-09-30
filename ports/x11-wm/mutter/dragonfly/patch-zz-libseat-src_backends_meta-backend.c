--- src/backends/meta-backend.c.orig
+++ src/backends/meta-backend.c
@@ -92,7 +92,7 @@
 #endif
 
 
-#ifdef HAVE_LOGIND
+#ifdef HAVE_LAUNCHER
 #include "backends/meta-launcher.h"
 #endif
 
@@ -153,7 +153,7 @@
   MetaIdleManager *idle_manager;
   MetaRenderer *renderer;
   MetaColorManager *color_manager;
-#ifdef HAVE_LOGIND
+#ifdef HAVE_LAUNCHER
   MetaLauncher *launcher;
 #endif
 #ifdef HAVE_LIBGUDEV
@@ -277,7 +277,7 @@
   g_cancellable_cancel (priv->cancellable);
   g_clear_object (&priv->cancellable);
 
-#ifdef HAVE_LOGIND
+#ifdef HAVE_LAUNCHER
   g_clear_object (&priv->launcher);
 #endif
 
@@ -915,7 +915,7 @@
                   G_TYPE_UINT, 0);
 }
 
-#ifdef HAVE_LOGIND
+#ifdef HAVE_LAUNCHER
 void
 meta_backend_pause (MetaBackend *backend)
 {
@@ -1281,7 +1281,7 @@
              system_bus_gotten_cb,
              backend);
 
-#ifdef HAVE_LOGIND
+#ifdef HAVE_LAUNCHER
   if (!meta_backend_create_launcher (backend, &priv->launcher, error))
       return FALSE;
 #endif
@@ -1475,7 +1475,7 @@
   return priv->color_manager;
 }
 
-#ifdef HAVE_LOGIND
+#ifdef HAVE_LAUNCHER
 MetaLauncher *
 meta_backend_get_launcher (MetaBackend *backend)
 {
