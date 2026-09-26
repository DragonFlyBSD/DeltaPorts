--- gnome-session/gsm-manager.c.orig	2026-07-04 14:08:37 UTC
+++ gnome-session/gsm-manager.c
@@ -40,9 +40,14 @@
 #include "gsm-manager.h"
 #include "org.gnome.SessionManager.h"
 
+#ifdef ENABLE_SYSTEMD
 #include <systemd/sd-journal.h>
-
 #include <systemd/sd-daemon.h>
+#else
+/* No systemd on DragonFly: readiness/journal notifications are no-ops. */
+#define sd_notify(unset_env, state) do { } while (0)
+#define sd_journal_send(...) do { } while (0)
+#endif
 
 #include "gsm-app.h"
 #include "gsm-client.h"
@@ -847,6 +852,16 @@ gsm_manager_start (GsmManager *manager)
 
         debug_app_summary (manager);
         start_phase (manager);
+#ifndef ENABLE_SYSTEMD
+        /* Without systemd there is no gnome-session-initialized.target, and so
+         * nothing calls Initialized() to leave INITIALIZATION. Advance ourselves
+         * so the session reaches APPLICATION/RUNNING and org.gnome.SessionManager
+         * becomes usable (gsd helpers register against it). */
+        if (manager->phase == GSM_MANAGER_PHASE_INITIALIZATION) {
+                manager->phase++;
+                start_phase (manager);
+        }
+#endif
 }
 
 void
