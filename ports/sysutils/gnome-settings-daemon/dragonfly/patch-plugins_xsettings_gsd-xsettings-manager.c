--- plugins/xsettings/gsd-xsettings-manager.c.orig
+++ plugins/xsettings/gsd-xsettings-manager.c
@@ -301,6 +301,7 @@
 
         GDBusNodeInfo     *introspection_data;
         guint              gtk_settings_name_id;
+        guint              gtk_settings_registration_id;
 };
 
 static void     gsd_xsettings_manager_class_init  (GsdXSettingsManagerClass *klass);
@@ -1451,6 +1452,18 @@
         G_APPLICATION_CLASS (gsd_xsettings_manager_parent_class)->startup (app);
 
         gnome_settings_profile_end (NULL);
+}
+
+static void
+unregister_gtk_settings (GsdXSettingsManager *manager,
+                         GDBusConnection     *connection)
+{
+        g_clear_handle_id (&manager->gtk_settings_name_id, g_bus_unown_name);
+        if (manager->gtk_settings_registration_id != 0) {
+                g_warn_if_fail (g_dbus_connection_unregister_object (connection,
+                                                                   manager->gtk_settings_registration_id));
+                manager->gtk_settings_registration_id = 0;
+        }
 }
 
 static void
@@ -1460,6 +1473,9 @@
         GDBusConnection *connection = g_application_get_dbus_connection (G_APPLICATION (manager));
 
         g_debug ("Stopping xsettings manager");
+
+        /* GApplication drains pending D-Bus requests after shutdown. */
+        unregister_gtk_settings (manager, connection);
 
         if (manager->notify_idle_id) {
                 g_source_remove (manager->notify_idle_id);
@@ -1596,14 +1612,19 @@
         manager->introspection_data = g_dbus_node_info_new_for_xml (introspection_xml, NULL);
         g_assert (manager->introspection_data != NULL);
 
-        g_dbus_connection_register_object (connection,
+        manager->gtk_settings_registration_id = g_dbus_connection_register_object (connection,
                                            GTK_SETTINGS_DBUS_PATH,
                                            manager->introspection_data->interfaces[0],
                                            &interface_vtable,
                                            manager,
                                            NULL,
-                                           NULL);
+                                           error);
 
+        if (manager->gtk_settings_registration_id == 0) {
+                g_clear_pointer (&manager->introspection_data, g_dbus_node_info_unref);
+                return FALSE;
+        }
+
         manager->gtk_settings_name_id = g_bus_own_name_on_connection (connection,
                                                                       GTK_SETTINGS_DBUS_NAME,
                                                                       G_BUS_NAME_OWNER_FLAGS_NONE,
@@ -1619,10 +1640,9 @@
 {
         GsdXSettingsManager *manager = GSD_XSETTINGS_MANAGER (app);
 
+        unregister_gtk_settings (manager, connection);
         g_clear_pointer (&manager->introspection_data, g_dbus_node_info_unref);
 
-        g_clear_handle_id (&manager->gtk_settings_name_id, g_bus_unown_name);
-
         G_APPLICATION_CLASS (gsd_xsettings_manager_parent_class)->dbus_unregister (app,
                                                                                    connection,
                                                                                    object_path);
