--- src/wayland/meta-wayland.c.orig
+++ src/wayland/meta-wayland.c
@@ -778,13 +778,21 @@ set_gnome_env (const char *name,
         {
           return update_activation_environment (session_bus, name, value);
         }
-      else if (g_strcmp0 (remote_error, "org.gnome.SessionManager.NotInInitialization") != 0)
+      else if (g_strcmp0 (remote_error,
+                          "org.gnome.SessionManager.NotInInitialization") == 0)
+        {
+#ifdef __DragonFly__
+          return update_activation_environment (session_bus, name, value);
+#else
+          return FALSE;
+#endif
+        }
+      else
         {
           g_warning ("Failed to set environment variable %s for gnome-session: %s",
                      name, error->message);
+          return FALSE;
         }
-
-      return FALSE;
     }
   return TRUE;
 }
