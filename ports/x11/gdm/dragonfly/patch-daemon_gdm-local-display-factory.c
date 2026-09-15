--- daemon/gdm-local-display-factory.c.orig
+++ daemon/gdm-local-display-factory.c
@@ -391,6 +391,14 @@
 
         gboolean is_settled = FALSE;
 
+#ifdef __DragonFly__
+        /* libudev-devd exposes no master-of-seat tags to enumerate, and the
+         * DRM driver is loaded from loader.conf/rc long before gdm starts;
+         * waiting for uevents would only delay every boot by
+         * SEAT0_GRAPHICS_CHECK_TIMEOUT. */
+        return TRUE;
+#endif
+
         if (factory->seat0_has_platform_graphics) {
                 g_debug ("GdmLocalDisplayFactory: udev settled, platform graphics enabled.");
                 return TRUE;
@@ -659,7 +667,15 @@
         if (!result) {
                 g_warning ("GdmLocalDisplayFactory: Failed to issue method call: %s", error->message);
                 g_clear_error (&error);
+#ifdef __DragonFly__
+                /* There is no logind to enumerate seats from; this platform
+                 * has exactly one static seat. */
+                g_debug ("GdmLocalDisplayFactory: no logind, ensuring display for static seat0");
+                ensure_display_for_seat (factory, "seat0");
+                return TRUE;
+#else
                 return FALSE;
+#endif
         }
 
         array = g_variant_get_child_value (result, 0);
