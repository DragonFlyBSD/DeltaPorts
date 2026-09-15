diff -urN orig/gtk/gtkfilechoosernativeportal.c patched/gtk/gtkfilechoosernativeportal.c
--- gtk/gtkfilechoosernativeportal.c	2026-07-23 08:45:57.orig
+++ gtk/gtkfilechoosernativeportal.c	2026-07-23 08:46:18
@@ -485,6 +485,15 @@
   else
     display = gdk_display_get_default ();
 
+#ifdef __DragonFly__
+  /*
+   * The DragonFly Documents portal has no usable FUSE mount.  Native GTK
+   * applications must therefore use the fallback chooser, which returns the
+   * original GVfs URI instead of a portal document path.
+   */
+  return FALSE;
+#endif
+
   if (!gdk_display_should_use_portal (display, PORTAL_FILECHOOSER_INTERFACE, 3))
     return FALSE;
 
diff -urN orig/testsuite/gtk/filechoosernative.c patched/testsuite/gtk/filechoosernative.c
--- testsuite/gtk/filechoosernative.c	1970-01-01 08:00:00.orig
+++ testsuite/gtk/filechoosernative.c	2026-07-23 08:53:15
@@ -0,0 +1,33 @@
+#include <gtk/gtk.h>
+
+#include "../gtk/gtkfilechoosernativeprivate.h"
+
+G_GNUC_BEGIN_IGNORE_DEPRECATIONS
+
+static void
+test_portal_fallback (void)
+{
+  GtkFileChooserNative *chooser;
+
+  chooser = gtk_file_chooser_native_new ("Open",
+                                         NULL,
+                                         GTK_FILE_CHOOSER_ACTION_OPEN,
+                                         "Open",
+                                         "Cancel");
+
+  g_assert_false (gtk_file_chooser_native_portal_show (chooser));
+
+  g_object_unref (chooser);
+}
+
+int
+main (int argc, char *argv[])
+{
+  g_test_init (&argc, &argv, NULL);
+
+  g_test_add_func ("/filechooser/native/portal-fallback", test_portal_fallback);
+
+  return g_test_run ();
+}
+
+G_GNUC_END_IGNORE_DEPRECATIONS
diff -urN orig/testsuite/gtk/meson.build patched/testsuite/gtk/meson.build
--- testsuite/gtk/meson.build	2026-07-23 08:45:57.orig
+++ testsuite/gtk/meson.build	2026-07-23 08:46:18
@@ -141,6 +141,12 @@
   { 'name': 'bitmask' },
 ]
 
+if host_machine.system() == 'dragonfly'
+  internal_tests += [
+    { 'name': 'filechoosernative' },
+  ]
+endif
+
 is_debug = get_option('buildtype').startswith('debug')
 
 test_cargs = []
diff -urN orig/testsuite/headless/meson.build patched/testsuite/headless/meson.build
--- testsuite/headless/meson.build	2026-07-23 08:50:48.orig
+++ testsuite/headless/meson.build	2026-07-23 08:51:01
@@ -20,10 +20,12 @@
     env: env,
   )
 
-  test('waylandsocket',
-    find_program('run-headless-wayland-tests.sh', dirs: meson.current_source_dir()),
-    args: [test_executables['waylandsocket'].full_path()],
-    suite: ['headless'],
-    env: env,
-  )
+  if os_linux
+    test('waylandsocket',
+      find_program('run-headless-wayland-tests.sh', dirs: meson.current_source_dir()),
+      args: [test_executables['waylandsocket'].full_path()],
+      suite: ['headless'],
+      env: env,
+    )
+  endif
 endif
