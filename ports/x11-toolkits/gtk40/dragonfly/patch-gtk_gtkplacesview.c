--- gtk/gtkplacesview.c.orig
+++ gtk/gtkplacesview.c
@@ -936,17 +936,25 @@
     {
       file = g_file_enumerator_get_child (enumerator, l->data);
       type = g_file_info_get_file_type (l->data);
-      if (type == G_FILE_TYPE_SHORTCUT || type == G_FILE_TYPE_MOUNTABLE)
-        uri = g_file_info_get_attribute_as_string (l->data, G_FILE_ATTRIBUTE_STANDARD_TARGET_URI);
+      if (type == G_FILE_TYPE_MOUNTABLE)
+        {
+          /* GVfs mountables carry a virtual GFile that must be mounted first. */
+          activatable_file = g_object_ref (file);
+        }
       else
-        uri = g_file_get_uri (file);
-      activatable_file = g_file_new_for_uri (uri);
+        {
+          if (type == G_FILE_TYPE_SHORTCUT)
+            uri = g_file_info_get_attribute_as_string (l->data, G_FILE_ATTRIBUTE_STANDARD_TARGET_URI);
+          else
+            uri = g_file_get_uri (file);
+          activatable_file = g_file_new_for_uri (uri);
+          g_free (uri);
+        }
       display_name = g_file_info_get_attribute_as_string (l->data, G_FILE_ATTRIBUTE_STANDARD_DISPLAY_NAME);
       icon = g_file_info_get_icon (l->data);
 
       add_file (view, activatable_file, icon, display_name, NULL, TRUE);
 
-      g_free (uri);
       g_free (display_name);
       g_clear_object (&file);
       g_clear_object (&activatable_file);
