--- src/backends/meta-backlight-sysfs.c.orig
+++ src/backends/meta-backlight-sysfs.c
@@ -285,6 +285,8 @@
     return NULL;
 
   session_proxy = meta_launcher_get_session_proxy (launcher);
+  if (!session_proxy)
+    return NULL;
 
   if (!meta_dbus_login1_session_call_set_brightness_sync (session_proxy,
                                                           "", "", 0,
