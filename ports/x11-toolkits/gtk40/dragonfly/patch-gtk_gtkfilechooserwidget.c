--- gtk/gtkfilechooserwidget.c.orig
+++ gtk/gtkfilechooserwidget.c
@@ -4185,6 +4185,39 @@
 }
 
 static void
+update_current_folder_mount_mountable_cb (GObject      *source,
+                                          GAsyncResult *result,
+                                          gpointer      user_data)
+{
+  GFile *file = G_FILE (source);
+  struct UpdateCurrentFolderData *data = user_data;
+  GtkFileChooserWidget *impl = data->impl;
+  GFile *mounted_file = NULL;
+  GError *error = NULL;
+
+  g_clear_object (&impl->update_current_folder_cancellable);
+  set_busy_cursor (impl, FALSE);
+
+  mounted_file = g_file_mount_mountable_finish (file, result, &error);
+  if (error)
+    {
+      error_changing_folder_dialog (data->impl, data->file, g_error_copy (error));
+      impl->reload_state = RELOAD_EMPTY;
+    }
+  else
+    {
+      change_folder_and_display_error (impl, mounted_file, data->clear_entry);
+    }
+
+  g_clear_object (&mounted_file);
+  g_object_unref (data->impl);
+  g_object_unref (data->file);
+  g_free (data);
+
+  g_clear_error (&error);
+}
+
+static void
 update_current_folder_get_info_cb (GObject      *source,
                                    GAsyncResult *result,
                                    gpointer      user_data)
@@ -4213,6 +4246,28 @@
       g_clear_object (&info);
     }
 
+  if (info != NULL && g_file_info_get_file_type (info) == G_FILE_TYPE_MOUNTABLE)
+    {
+      GMountOperation *mount_operation;
+      GtkWidget *toplevel;
+
+      toplevel = GTK_WIDGET (gtk_widget_get_root (GTK_WIDGET (impl)));
+      mount_operation = gtk_mount_operation_new (GTK_WINDOW (toplevel));
+
+      g_clear_object (&info);
+      set_busy_cursor (impl, TRUE);
+
+      impl->update_current_folder_cancellable = g_cancellable_new ();
+      g_file_mount_mountable (data->file,
+                               G_MOUNT_MOUNT_NONE,
+                               mount_operation,
+                               impl->update_current_folder_cancellable,
+                               update_current_folder_mount_mountable_cb,
+                               data);
+
+      return;
+    }
+
   if (error ||
       !g_file_info_has_attribute (info, G_FILE_ATTRIBUTE_STANDARD_TYPE))
     {
