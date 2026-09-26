--- gnome-session/gsm-system.c.orig	2026-07-04 14:08:37 UTC
+++ gnome-session/gsm-system.c
@@ -23,7 +23,9 @@
 
 #include "gsm-system.h"
 
+#ifdef ENABLE_SYSTEMD
 #include "gsm-systemd.h"
+#endif
 
 
 enum {
@@ -197,12 +199,14 @@ gsm_get_system (void)
 {
         static GsmSystem *system = NULL;
 
+#ifdef ENABLE_SYSTEMD
         if (system == NULL) {
                 system = GSM_SYSTEM (gsm_systemd_new ());
                 if (system != NULL) {
                         g_debug ("Using systemd for session tracking");
                 }
         }
+#endif
 
         if (system == NULL) {
                 system = g_object_new (gsm_system_null_get_type (), NULL);
