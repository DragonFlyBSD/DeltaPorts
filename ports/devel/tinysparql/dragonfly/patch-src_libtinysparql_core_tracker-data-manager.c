--- src/libtinysparql/core/tracker-data-manager.c.orig
+++ src/libtinysparql/core/tracker-data-manager.c
@@ -983,8 +983,9 @@
 {
 	if (tracker_property_get_data_type (property) == TRACKER_PROPERTY_TYPE_RESOURCE &&
 	    !increase_refcount (manager, iface, graph,
-	                        NULL, tracker_class_get_name (class),
+	                        tracker_class_get_name (class),
 	                        tracker_property_get_name (property),
+	                        tracker_property_get_name (property),
 	                        "LIMIT 1",
 	                        error))
 		return FALSE;
