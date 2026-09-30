--- src/backends/native/meta-seat-impl.c.orig
+++ src/backends/native/meta-seat-impl.c
@@ -3077,7 +3077,9 @@
 {
   struct udev *udev;
   struct libinput *libinput;
+#ifdef HAVE_LIBINPUT_PLUGIN_SYSTEM
   char xdg[PATH_MAX] = {0};
+#endif
 
   udev = udev_new ();
   if (G_UNLIKELY (udev == NULL))
@@ -3098,11 +3100,13 @@
       return FALSE;
     }
 
+#ifdef HAVE_LIBINPUT_PLUGIN_SYSTEM
   g_snprintf (xdg, sizeof xdg, "%s/libinput/plugins", g_get_user_config_dir ());
 
   libinput_plugin_system_append_path (libinput, xdg);
   libinput_plugin_system_append_default_paths (libinput);
   libinput_plugin_system_load_plugins (libinput, LIBINPUT_PLUGIN_SYSTEM_FLAG_NONE);
+#endif
 
   if (libinput_udev_assign_seat (libinput, seat_impl->seat_id) == -1)
     {
