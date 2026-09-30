--- daemon/gdm-manager.c.orig
+++ daemon/gdm-manager.c
@@ -28,6 +28,7 @@
 #include <signal.h>
 #include <sys/stat.h>
 #include <sys/types.h>
+#include <pwd.h>
 
 #include <glib.h>
 #include <glib/gi18n.h>
@@ -302,11 +303,23 @@
 session_unlock (GdmManager *manager,
                 const char *ssid)
 {
+#ifndef __DragonFly__
         GError *error = NULL;
         GVariant *reply;
+#endif
 
         g_debug ("Unlocking session %s", ssid);
 
+#ifdef __DragonFly__
+        /* No logind on DragonFly: UnlockSession over org.freedesktop.login1
+         * has no session to unlock. The actual screen unlock is driven by
+         * gnome-shell once PAM reauthentication succeeds; this call only mirrors
+         * that state into logind, which is absent. Return TRUE so the
+         * reauthentication completion path does not log spurious failures. */
+        (void) manager;
+        (void) ssid;
+        return TRUE;
+#else
         reply = g_dbus_connection_call_sync (manager->connection,
                                              "org.freedesktop.login1",
                                              "/org/freedesktop/login1",
@@ -328,6 +341,7 @@
         g_variant_unref (reply);
 
         return TRUE;
+#endif
 }
 
 static GdmSession *
@@ -450,6 +464,32 @@
         return out_tty;
 }
 
+#ifdef __DragonFly__
+static gboolean
+lookup_by_session_uid (const char *id,
+                       GdmDisplay *display,
+                       gpointer    user_data)
+{
+        uid_t         *uid = user_data;
+        GdmSession    *session;
+        const char    *username;
+        struct passwd *pwent = NULL;
+
+        session = g_object_get_data (G_OBJECT (display), "gdm-user-session");
+        if (session == NULL)
+                return FALSE;
+
+        username = gdm_session_get_username (session);
+        if (username == NULL)
+                return FALSE;
+
+        if (!gdm_get_pwent_for_name (username, &pwent) || pwent == NULL)
+                return FALSE;
+
+        return pwent->pw_uid == *uid;
+}
+#endif
+
 static void
 get_display_and_details_for_bus_sender (GdmManager       *self,
                                         GDBusConnection  *connection,
@@ -491,6 +531,51 @@
                 g_error_free (error);
                 goto out;
         }
+
+#ifdef __DragonFly__
+        /* No logind session tracking on this platform: resolve the caller
+         * to a display by matching its uid against the owner of each
+         * display's user session. One static seat keeps this unambiguous. */
+        (void) session_uid;
+        display = gdm_display_store_find (self->display_store,
+                                          lookup_by_session_uid,
+                                          &caller_uid);
+        if (out_uid != NULL)
+                *out_uid = caller_uid;
+        if (out_is_login_screen != NULL)
+                *out_is_login_screen = FALSE;
+        if (out_is_remote != NULL)
+                *out_is_remote = FALSE;
+        /* Provide the session id, seat and a usable pid too. The
+         * reauthentication path (screen-lock unlock) rejects the caller with
+         * "No session available" unless session_id is non-NULL AND pid is
+         * non-zero, and unlock is reauth-only so it cannot fall back to a fresh
+         * login. Two DragonFly gaps trip that check:
+         *   - the user session's display has no logind conversation session id
+         *     (it is NULL), and
+         *   - DragonFly's D-Bus cannot report a peer pid -- the platform xucred
+         *     carries no cr_pid -- so gdm_dbus_get_pid_for_name returned 0.
+         * reauthentication uses pid solely as an association key (the manager
+         * and worker hash tables, matched back in the started-callback), never
+         * as a process handle: PAM re-verifies by uid. So synthesize a non-NULL
+         * session id, and rewrite a zero pid to a stable non-zero key derived
+         * from the uid. Screen unlock is serial per user, so the key stays
+         * unambiguous. */
+        if (display != NULL) {
+                if (out_session_id != NULL) {
+                        const char *sid = gdm_display_get_session_id (display);
+                        *out_session_id = (sid != NULL && sid[0] != '\0')
+                                          ? g_strdup (sid)
+                                          : g_strdup_printf ("gdm-user-%d",
+                                                             (int) caller_uid);
+                }
+                if (out_seat_id != NULL)
+                        *out_seat_id = g_strdup ("seat0");
+        }
+        if (out_pid != NULL && pid == 0)
+                *out_pid = (GPid) (caller_uid != 0 ? caller_uid : 1);
+        goto out;
+#endif
 
         ret = gdm_find_display_session (pid, caller_uid, &session_id, &error);
 
