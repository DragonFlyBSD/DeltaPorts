--- gio/gunixvolume.c.orig
+++ gio/gunixvolume.c
@@ -376,6 +376,10 @@
   GUnixVolume *unix_volume = G_UNIX_VOLUME (volume);
+#if defined(__FreeBSD__) || defined(__DragonFly__)
+  const gchar *argv[] = { "cdcontrol", "-f", NULL, "eject", NULL };
+  argv[2] = unix_volume->device_path;
+#else
   const gchar *argv[] = { "eject", NULL, NULL };
-
   argv[1] = unix_volume->device_path;
-
+#endif
+
   eject_mount_do (volume, cancellable, callback, user_data, argv, "[gio] eject volume");
