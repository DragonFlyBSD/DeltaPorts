--- daemon/gdm-wayland-session.c.orig
+++ daemon/gdm-wayland-session.c
@@ -105,6 +105,10 @@
         GError              *error = NULL;
         char                *bus_address_fd_string = NULL;
         char                *bus_address = NULL;
+#ifdef __DragonFly__
+        const char          *runtime_dir;
+        char                *bus_address_argument = NULL;
+#endif
         gsize                bus_address_size;
 
         gboolean  is_running = FALSE;
@@ -141,12 +145,23 @@
         g_ptr_array_add (arguments, "--print-address");
         g_ptr_array_add (arguments, bus_address_fd_string);
         g_ptr_array_add (arguments, "--session");
+#ifdef __DragonFly__
+        runtime_dir = g_getenv ("XDG_RUNTIME_DIR");
+        if (runtime_dir != NULL && runtime_dir[0] != '\0') {
+                bus_address_argument = g_strdup_printf ("unix:path=%s/bus", runtime_dir);
+                g_ptr_array_add (arguments, "--address");
+                g_ptr_array_add (arguments, bus_address_argument);
+        }
+#endif
         g_ptr_array_add (arguments, NULL);
 
         subprocess = g_subprocess_launcher_spawnv (launcher,
                                                    (const char * const *) arguments->pdata,
                                                    &error);
         g_free (bus_address_fd_string);
+#ifdef __DragonFly__
+        g_free (bus_address_argument);
+#endif
         g_clear_object (&launcher);
         g_ptr_array_free (arguments, TRUE);
 
