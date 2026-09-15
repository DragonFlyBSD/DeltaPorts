--- panels/color/cc-color-calibrate.c.orig
+++ panels/color/cc-color-calibrate.c
@@ -789,9 +789,9 @@
       calibrate->proxy_inhibit = g_dbus_proxy_new_for_bus_sync (G_BUS_TYPE_SYSTEM,
                                                                 G_DBUS_PROXY_FLAGS_NONE,
                                                                 NULL,
-                                                                "org.freedesktop.login1",
-                                                                "/org/freedesktop/login1",
-                                                                "org.freedesktop.login1.Manager",
+                                                                "org.freedesktop.ConsoleKit",
+                                                                "/org/freedesktop/ConsoleKit/Manager",
+                                                                "org.freedesktop.ConsoleKit.Manager",
                                                                 NULL,
                                                                 error);
       if (calibrate->proxy_inhibit == NULL)
