--- src/backends/meta-stage-impl.c.orig
+++ src/backends/meta-stage-impl.c
@@ -281,7 +281,9 @@
       priv->global_frame_counter++;
 
       n_rects = mtk_region_num_rectangles (swap_region);
-      if (n_rects > 0 && !swap_with_damage)
+      if (n_rects > 0 && !swap_with_damage &&
+          cogl_context_has_winsys_feature (cogl_context,
+                                           COGL_WINSYS_FEATURE_SWAP_REGION))
         {
           meta_topic (META_DEBUG_BACKEND,
                       "cogl_onscreen_swap_region (onscreen: %p)",
