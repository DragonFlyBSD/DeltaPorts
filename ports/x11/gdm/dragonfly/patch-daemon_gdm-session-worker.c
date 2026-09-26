--- daemon/gdm-session-worker.c.orig
+++ daemon/gdm-session-worker.c
@@ -29,8 +29,11 @@
 #include <sys/types.h>
 #include <sys/wait.h>
 #include <sys/ioctl.h>
+#include <sys/stat.h>
+#ifdef __linux__
 #include <sys/vt.h>
 #include <sys/kd.h>
+#endif
 #include <errno.h>
 #include <grp.h>
 #include <pwd.h>
@@ -969,27 +972,35 @@
 static void
 on_release_display (int signal)
 {
+#ifdef __linux__
         int fd;
 
         fd = open ("/dev/tty0", O_RDWR | O_NOCTTY);
         ioctl(fd, VT_RELDISP, 1);
         close(fd);
+#endif
 }
 
 static void
 on_acquire_display (int signal)
 {
+#ifdef __linux__
         int fd;
 
         fd = open ("/dev/tty0", O_RDWR | O_NOCTTY);
         ioctl(fd, VT_RELDISP, VT_ACKACQ);
         close(fd);
+#endif
 }
 
 static gboolean
 handle_terminal_vt_switches (GdmSessionWorker *worker,
                              int               tty_fd)
 {
+#ifndef __linux__
+        /* VT switching stays with the kernel console and seatd. */
+        return TRUE;
+#else
         struct vt_mode setmode_request = { 0 };
         gboolean succeeded = TRUE;
 
@@ -1006,12 +1017,14 @@
         signal (ACQUIRE_DISPLAY_SIGNAL, on_acquire_display);
 
         return succeeded;
+#endif
 }
 
 static void
 fix_terminal_vt_mode (GdmSessionWorker  *worker,
                       int                tty_fd)
 {
+#ifdef __linux__
         struct vt_mode getmode_reply = { 0 };
         int kernel_display_mode = 0;
         gboolean mode_fixed = FALSE;
@@ -1048,12 +1061,18 @@
 
         g_debug ("GdmSessionWorker: VT mode did %sneed to be fixed",
                  mode_fixed? "" : "not ");
+#endif
 }
 
 static void
 jump_to_vt (GdmSessionWorker  *worker,
             int                vt_number)
 {
+#ifndef __linux__
+        /* VT switching stays with the kernel console and seatd. */
+        g_debug ("GdmSessionWorker: not jumping to VT %d (no VT handling on this OS)",
+                 vt_number);
+#else
         int fd;
         int active_vt_tty_fd;
         int active_vt = -1;
@@ -1107,6 +1126,7 @@
         }
 
         close (active_vt_tty_fd);
+#endif
 }
 
 static void
@@ -1461,6 +1481,41 @@
         gdm_session_worker_set_environment_variable (worker, "HOME", home);
         gdm_session_worker_set_environment_variable (worker, "PWD", home);
         gdm_session_worker_set_environment_variable (worker, "SHELL", shell);
+
+#ifdef __DragonFly__
+        /* There is no logind to provision XDG_RUNTIME_DIR. Create the
+         * conventional per-uid directory on tmpfs ourselves; the worker
+         * still runs as root at this point. Refuse to adopt a directory
+         * owned by anybody else (/tmp is sticky and world-writable). */
+        {
+                g_autofree char *runtime_dir = NULL;
+                struct stat runtime_dir_stat;
+
+                runtime_dir = g_strdup_printf ("/tmp/runtime-%u", (unsigned int) uid);
+
+                if (mkdir (runtime_dir, 0700) == 0) {
+                        if (chown (runtime_dir, uid, gid) < 0) {
+                                g_warning ("GdmSessionWorker: could not chown %s: %m",
+                                           runtime_dir);
+                        }
+                } else if (errno != EEXIST) {
+                        g_warning ("GdmSessionWorker: could not create %s: %m",
+                                   runtime_dir);
+                }
+
+                if (lstat (runtime_dir, &runtime_dir_stat) == 0 &&
+                    S_ISDIR (runtime_dir_stat.st_mode) &&
+                    runtime_dir_stat.st_uid == uid) {
+                        gdm_session_worker_set_environment_variable (worker,
+                                                                     "XDG_RUNTIME_DIR",
+                                                                     runtime_dir);
+                } else {
+                        g_warning ("GdmSessionWorker: not exporting XDG_RUNTIME_DIR: "
+                                   "%s is not a directory owned by uid %u",
+                                   runtime_dir, (unsigned int) uid);
+                }
+        }
+#endif
 }
 
 static gboolean
@@ -2163,6 +2218,11 @@
 static gboolean
 set_up_for_new_vt (GdmSessionWorker *worker)
 {
+#ifndef __linux__
+        /* VT allocation and switching stay with the kernel console and
+         * seatd on DragonFly; there is nothing for GDM to set up. */
+        return TRUE;
+#else
         int initial_vt_fd;
         char vt_string[256], tty_string[256];
         int session_vt = 0;
@@ -2224,6 +2284,7 @@
 fail:
         close (initial_vt_fd);
         return FALSE;
+#endif
 }
 
 static gboolean
@@ -2821,10 +2882,13 @@
 {
         g_autoptr(GPtrArray) array = NULL;
         g_auto(GStrv) filtered_extensions = NULL;
+#ifdef SUPPORTS_PAM_EXTENSIONS
         size_t i, j;
+#endif
 
         array = g_ptr_array_new_with_free_func (g_free);
 
+#ifdef SUPPORTS_PAM_EXTENSIONS
         for (i = 0; extensions[i] != NULL; i++) {
                 for (j = 0; gdm_supported_pam_extensions[j] != NULL; j++) {
                         if (g_strcmp0 (extensions[i], gdm_supported_pam_extensions[j]) == 0) {
@@ -2833,6 +2897,9 @@
                         }
                 }
         }
+#else
+        (void) extensions;
+#endif
         g_ptr_array_add (array, NULL);
 
         filtered_extensions = g_strdupv ((char **) array->pdata);
