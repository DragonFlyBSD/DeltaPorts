--- document-portal/file-transfer.c.orig
+++ document-portal/file-transfer.c
@@ -35,6 +35,19 @@
 #include <unistd.h>
 #include <fcntl.h>
 
+/* DragonFly lacks O_PATH and AT_EMPTY_PATH. The document portal (with its
+ * FUSE reflection through /proc/self/fd) is a Linux-only facility; on
+ * DragonFly it is built but never dbus-activated (nothing without flatpak
+ * needs it). Define the missing flags as 0 so the daemon compiles; the
+ * affected opens/stats degrade harmlessly since the path is never taken.
+ * O_TMPFILE and renameat2 flags are already #ifdef/HAVE_ guarded upstream. */
+#ifndef O_PATH
+#define O_PATH 0
+#endif
+#ifndef AT_EMPTY_PATH
+#define AT_EMPTY_PATH 0
+#endif
+
 #include <gio/gio.h>
 #include <gio/gunixfdlist.h>
 
