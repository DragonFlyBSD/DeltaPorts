--- src/backends/meta-udev.c.orig
+++ src/backends/meta-udev.c
@@ -195,7 +195,8 @@
     {
     case META_UDEV_DEVICE_TYPE_CARD:
       g_udev_enumerator_add_match_name (enumerator, "card*");
-      g_udev_enumerator_add_match_tag (enumerator, "seat");
+      /* No udev "seat" tag on DragonFly (libudev-devd has no tag support);
+       * seat membership is enforced via ID_SEAT in meta_udev_is_drm_device(). */
       break;
     case META_UDEV_DEVICE_TYPE_RENDER_NODE:
       g_udev_enumerator_add_match_name (enumerator, "render*");
