--- gio/gunixmount.c.orig
+++ gio/gunixmount.c
@@ -367,2 +367,3 @@
   GUnixMount *unix_mount = G_UNIX_MOUNT (mount);
+#if !defined(__FreeBSD__) && !defined(__DragonFly__)
   char *argv[] = {"eject", NULL, NULL};
@@ -375,2 +376,3 @@
   eject_unmount_do (mount, cancellable, callback, user_data, argv, "[gio] eject mount");
+#endif
 }
