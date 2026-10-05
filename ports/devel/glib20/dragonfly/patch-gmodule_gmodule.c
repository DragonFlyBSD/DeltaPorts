--- gmodule/gmodule.c.orig
+++ gmodule/gmodule.c
@@ -631,12 +631,16 @@
     {
       gchar *real_name = parse_libtool_archive (name);
-
+
-      /* real_name might be NULL, but then module error is already set */
-      if (real_name)
+      if (real_name == NULL)
         {
-          g_free (name);
-          name = real_name;
+          g_set_error_literal (error, G_MODULE_ERROR, G_MODULE_ERROR_FAILED,
+                               g_module_error ());
+          g_free (name);
+          g_rec_mutex_unlock (&g_module_global_lock);
+          return NULL;
         }
+      g_free (name);
+      name = real_name;
     }
-
+
   handle = _g_module_open (name, (flags & G_MODULE_BIND_LAZY) != 0,
