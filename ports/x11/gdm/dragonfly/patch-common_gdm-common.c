--- common/gdm-common.c.orig
+++ common/gdm-common.c
@@ -372,13 +372,25 @@
                             const char      *seat_id,
                             const char      *session_id)
 {
+#ifndef __DragonFly__
         GError *local_error = NULL;
         GVariant *reply;
+#endif
 
         g_return_val_if_fail (G_IS_DBUS_CONNECTION (connection), FALSE);
         g_return_val_if_fail (seat_id != NULL, FALSE);
         g_return_val_if_fail (session_id != NULL, FALSE);
 
+#ifdef __DragonFly__
+        /* No logind on DragonFly: there is a single static seat and the user
+         * session is already the foreground session, so ActivateSessionOnSeat
+         * is a no-op. The org.freedesktop.login1 service does not exist, so the
+         * real call fails -- return TRUE so callers complete instead of
+         * aborting, notably screen-unlock reauthentication reaching this via
+         * switch_to_compatible_user_session after PAM already succeeded. */
+        (void) cancellable;
+        return TRUE;
+#else
         reply = g_dbus_connection_call_sync (connection,
                                              "org.freedesktop.login1",
                                              "/org/freedesktop/login1",
@@ -398,6 +410,7 @@
         g_variant_unref (reply);
 
         return TRUE;
+#endif
 }
 
 gboolean
@@ -405,12 +418,25 @@
                              GCancellable    *cancellable,
                              const char      *session_id)
 {
+#ifndef __DragonFly__
         GError *local_error = NULL;
         GVariant *reply;
+#endif
 
         g_return_val_if_fail (G_IS_DBUS_CONNECTION (connection), FALSE);
         g_return_val_if_fail (session_id != NULL, FALSE);
 
+#ifdef __DragonFly__
+        /* No logind on DragonFly: sessions are not tracked by
+         * org.freedesktop.login1, so there is nothing to TerminateSession --
+         * a session's processes are reaped by gnome-session exiting and the
+         * session leader's own cleanup. Return TRUE so callers that terminate a
+         * stale/conflicting session before a new login proceed instead of
+         * treating the missing service as a failure. */
+        (void) cancellable;
+        (void) session_id;
+        return TRUE;
+#else
         reply = g_dbus_connection_call_sync (connection,
                                              "org.freedesktop.login1",
                                              "/org/freedesktop/login1",
@@ -430,6 +456,7 @@
         g_variant_unref (reply);
 
         return TRUE;
+#endif
 }
 
 gboolean
