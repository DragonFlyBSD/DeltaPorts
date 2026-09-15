--- src/tracker-extract/tracker-extract-raw.c.orig
+++ src/tracker-extract/tracker-extract-raw.c
@@ -23,6 +23,25 @@
 
 #include <gexiv2/gexiv2.h>
 
+/* gexiv2 0.16 moved the GError-less getters to deprecated try_* names and
+ * gave the plain names new signatures (extra GError**, and the GPS getters
+ * return the value instead of using an out parameter).  Map the plain names
+ * this file uses back to the try_* variants, which keep the exact 0.14
+ * semantics. */
+#if GEXIV2_CHECK_VERSION(0, 16, 0)
+#define gexiv2_metadata_get_tag_string(m, t)      gexiv2_metadata_try_get_tag_string ((m), (t), NULL)
+#define gexiv2_metadata_has_tag(m, t)             gexiv2_metadata_try_has_tag ((m), (t), NULL)
+#define gexiv2_metadata_get_tag_long(m, t)        gexiv2_metadata_try_get_tag_long ((m), (t), NULL)
+#define gexiv2_metadata_get_exposure_time(m, n, d) gexiv2_metadata_try_get_exposure_time ((m), (n), (d), NULL)
+#define gexiv2_metadata_get_fnumber(m)            gexiv2_metadata_try_get_fnumber ((m), NULL)
+#define gexiv2_metadata_get_focal_length(m)       gexiv2_metadata_try_get_focal_length ((m), NULL)
+#define gexiv2_metadata_get_iso_speed(m)          gexiv2_metadata_try_get_iso_speed ((m), NULL)
+#define gexiv2_metadata_get_gps_altitude(m, v)    gexiv2_metadata_try_get_gps_altitude ((m), (v), NULL)
+#define gexiv2_metadata_get_gps_latitude(m, v)    gexiv2_metadata_try_get_gps_latitude ((m), (v), NULL)
+#define gexiv2_metadata_get_gps_longitude(m, v)   gexiv2_metadata_try_get_gps_longitude ((m), (v), NULL)
+#define gexiv2_metadata_get_orientation(m)        gexiv2_metadata_try_get_orientation ((m), NULL)
+#endif
+
 #include <libtracker-extract/tracker-extract.h>
 #include <libtracker-miners-common/tracker-common.h>
 
