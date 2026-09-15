--- document-portal/document-portal-fuse.c.orig
+++ document-portal/document-portal-fuse.c
@@ -33,6 +33,19 @@
 #include <string.h>
 #include <errno.h>
 #include <fcntl.h>
+
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
 #include <stdlib.h>
 #include <assert.h>
 #include <glib/gprintf.h>
@@ -3283,7 +3296,14 @@
 #if defined(HAVE_SYS_XATTR_H)
       res = listxattr (path, buf, size);
 #elif defined(HAVE_SYS_EXTATTR_H)
+#ifdef __DragonFly__
+      /* DragonFly libc has no extattr_list_file (only get/set/delete) and
+       * the kernel has no extattr listing syscall; report unsupported. */
+      errno = ENOTSUP;
+      res = -1;
+#else
       res = extattr_list_file (path, EXTATTR_NAMESPACE_USER, buf, size);
+#endif
 #else
 #error "Not implemented for your platform"
 #endif
@@ -3502,11 +3502,15 @@ G_DEFINE_AUTO_CLEANUP_CLEAR_FUNC (XdpAutoFuseArgs, fuse_opt_free_args);
 static gpointer
 xdp_fuse_thread (gpointer data)
 {
-  /* Options:
-   *  auto_unmount: Tell fusermount to auto unmount if we die.
-   */
   static char *fusermount_argv[] = {
+#ifdef __DragonFly__
+    /* DragonFly libfuse does not implement the Linux auto_unmount option.
+     * xdp_fuse_exit() explicitly stops, unmounts, and destroys the session. */
+    "xdp-fuse", "-osubtype=portal,fsname=portal",
+#else
+    /* auto_unmount asks fusermount to release the mount after a crash. */
     "xdp-fuse", "-osubtype=portal,fsname=portal,auto_unmount",
+#endif
   };
   g_auto(XdpAutoFuseArgs) args =
     FUSE_ARGS_INIT (G_N_ELEMENTS (fusermount_argv), fusermount_argv);
