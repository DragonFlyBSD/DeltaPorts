--- src/core/window-private.h.orig
+++ src/core/window-private.h
@@ -528,7 +528,7 @@
   guint placed : 1;
 
   /* Have this window been positioned? */
-  uint unconstrained_rect_valid : 1;
+  guint unconstrained_rect_valid : 1;
 
   /* Has this window not ever been shown yet? */
   guint showing_for_first_time : 1;
