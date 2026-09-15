--- tools/gnome-session-ctl.c.orig	2026-07-04 14:10:37 UTC
+++ tools/gnome-session-ctl.c
@@ -28,7 +28,13 @@
 #include <errno.h>
 #include <sys/stat.h>
 #include <fcntl.h>
+#ifdef ENABLE_SYSTEMD
 #include <systemd/sd-daemon.h>
+#else
+/* No systemd on DragonFly: readiness notifications are no-ops. */
+#define sd_notify(unset_env, state) do { } while (0)
+#define sd_notifyf(unset_env, ...) do { } while (0)
+#endif
 
 #include <glib.h>
 #include <glib-unix.h>
