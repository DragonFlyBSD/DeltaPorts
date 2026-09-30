--- gnome-session/leader-dragonfly.c.orig	2026-07-04 15:27:25 UTC
+++ gnome-session/leader-dragonfly.c
@@ -0,0 +1,332 @@
+/* -*- Mode: C; tab-width: 8; indent-tabs-mode: nil; c-basic-offset: 8 -*-
+ *
+ * DragonFly (non-systemd) gnome-session init-worker.
+ *
+ * Upstream gnome-session 50 delegates the actual session bring-up to systemd
+ * user units (see leader-systemd.c, which does StartUnit gnome-session@X.target).
+ * DragonFly has no systemd, so this worker takes over that role directly:
+ *
+ *   1. start gnome-shell as the Wayland compositor / display server,
+ *   2. once the Wayland socket exists, start the session manager
+ *      (gnome-session-service), which provides org.gnome.SessionManager,
+ *   3. once that name owns the bus, start the gnome-settings-daemon helpers
+ *      (they register as session clients and would otherwise exit),
+ *   4. wait for the compositor to exit, then tear the session down.
+ *
+ * leader-main.c has already re-exec'd through a login shell and exported the
+ * activation environment to the session bus before exec'ing us.
+ */
+
+#include <config.h>
+
+#include <glib.h>
+#include <glib-unix.h>
+#include <gio/gio.h>
+
+#include <errno.h>
+#include <signal.h>
+#include <string.h>
+#include <sys/types.h>
+#include <sys/wait.h>
+#include <unistd.h>
+
+/* gnome-settings-daemon helpers to launch once the manager is up. Only the
+ * plugins actually built/installed on DragonFly (datetime and sharing stay
+ * disabled: they need timedated/NetworkManager). media-keys and sound are in
+ * now that libpulse (served by pipewire-pulse) is available. */
+static const char *const gsd_helpers[] = {
+        "power", "color", "keyboard", "a11y-settings",
+        "print-notifications", "smartcard", "screensaver-proxy",
+        "media-keys", "sound", NULL
+};
+
+static GPid
+launch (const char *const *args)
+{
+        GPid pid = 0;
+        g_autoptr (GError) error = NULL;
+
+        if (!g_spawn_async (NULL, (char **) args, NULL,
+                            G_SPAWN_SEARCH_PATH | G_SPAWN_DO_NOT_REAP_CHILD,
+                            NULL, NULL, &pid, &error)) {
+                g_warning ("Failed to launch %s: %s", args[0], error->message);
+                return 0;
+        }
+        return pid;
+}
+
+static gboolean
+wait_for_wayland (void)
+{
+        const char *runtime_dir = g_getenv ("XDG_RUNTIME_DIR");
+        const char *display = g_getenv ("WAYLAND_DISPLAY");
+        g_autofree char *socket_path = NULL;
+        int i;
+
+        if (display == NULL)
+                display = "wayland-0";
+        if (runtime_dir == NULL)
+                return FALSE;
+
+        socket_path = g_build_filename (runtime_dir, display, NULL);
+        for (i = 0; i < 150; i++) {     /* up to ~15s */
+                if (g_file_test (socket_path, G_FILE_TEST_EXISTS))
+                        return TRUE;
+                g_usleep (100 * 1000);
+        }
+        return FALSE;
+}
+
+static gboolean
+wait_for_name (const char *name)
+{
+        g_autoptr (GDBusConnection) bus = NULL;
+        int i;
+
+        bus = g_bus_get_sync (G_BUS_TYPE_SESSION, NULL, NULL);
+        if (bus == NULL)
+                return FALSE;
+
+        for (i = 0; i < 100; i++) {     /* up to ~10s */
+                g_autoptr (GVariant) reply = NULL;
+
+                reply = g_dbus_connection_call_sync (bus,
+                                                     "org.freedesktop.DBus",
+                                                     "/org/freedesktop/DBus",
+                                                     "org.freedesktop.DBus",
+                                                     "NameHasOwner",
+                                                     g_variant_new ("(s)", name),
+                                                     G_VARIANT_TYPE ("(b)"),
+                                                     G_DBUS_CALL_FLAGS_NONE,
+                                                     -1, NULL, NULL);
+                if (reply != NULL) {
+                        gboolean has_owner = FALSE;
+                        g_variant_get (reply, "(b)", &has_owner);
+                        if (has_owner)
+                                return TRUE;
+                }
+                g_usleep (100 * 1000);
+        }
+        return FALSE;
+}
+
+/* Mutter publishes the Xwayland DISPLAY/XAUTHORITY pair through the session
+ * bus activation environment. Start XSettings through D-Bus so it receives
+ * that dynamic environment instead of guessing an X display number. */
+static gboolean
+start_xsettings (void)
+{
+        g_autoptr (GDBusConnection) bus = NULL;
+        g_autoptr (GError) error = NULL;
+        g_autoptr (GVariant) reply = NULL;
+
+        bus = g_bus_get_sync (G_BUS_TYPE_SESSION, NULL, &error);
+        if (bus == NULL) {
+                g_warning ("Failed to connect to the session bus for XSettings: %s",
+                           error->message);
+                return FALSE;
+        }
+
+        reply = g_dbus_connection_call_sync (bus,
+                                             "org.freedesktop.DBus",
+                                             "/org/freedesktop/DBus",
+                                             "org.freedesktop.DBus",
+                                             "StartServiceByName",
+                                             g_variant_new ("(su)",
+                                                            "org.gnome.SettingsDaemon.XSettings",
+                                                            0),
+                                             G_VARIANT_TYPE ("(u)"),
+                                             G_DBUS_CALL_FLAGS_NONE,
+                                             -1, NULL, &error);
+        if (reply == NULL) {
+                g_warning ("Failed to start XSettings through D-Bus: %s",
+                           error->message);
+                return FALSE;
+        }
+
+        return TRUE;
+}
+
+static gint
+compare_names (gconstpointer a, gconstpointer b)
+{
+        return g_strcmp0 (*(const char *const *) a, *(const char *const *) b);
+}
+
+/* Apply systemd environment.d(5): $XDG_CONFIG_HOME/environment.d/*.conf (or
+ * ~/.config/environment.d), simple KEY=VALUE lines, later files winning. On a
+ * systemd host the user manager reads these into the session; there is none
+ * here, so the session leader does it. GDM hands the greeter its whole
+ * environment (DCONF_PROFILE, the greeter XDG_DATA_DIRS, GDM_SEAT_ID, ...)
+ * exclusively through this file, so without this the greeter shell starts
+ * with almost nothing and never brings up the login dialog. */
+static void
+load_environment_d (void)
+{
+        const char *config_home = g_getenv ("XDG_CONFIG_HOME");
+        g_autofree char *dir_path = NULL;
+        g_autoptr (GDir) dir = NULL;
+        g_autoptr (GPtrArray) names = NULL;
+        const char *name;
+
+        if (config_home != NULL && config_home[0] != '\0') {
+                dir_path = g_build_filename (config_home, "environment.d", NULL);
+        } else {
+                const char *home = g_getenv ("HOME");
+                if (home == NULL)
+                        return;
+                dir_path = g_build_filename (home, ".config", "environment.d", NULL);
+        }
+
+        dir = g_dir_open (dir_path, 0, NULL);
+        if (dir == NULL)
+                return;
+
+        names = g_ptr_array_new_with_free_func (g_free);
+        while ((name = g_dir_read_name (dir)) != NULL) {
+                if (g_str_has_suffix (name, ".conf"))
+                        g_ptr_array_add (names, g_strdup (name));
+        }
+        g_ptr_array_sort (names, compare_names);
+
+        for (guint i = 0; i < names->len; i++) {
+                g_autofree char *path = NULL;
+                g_autofree char *contents = NULL;
+                g_auto (GStrv) lines = NULL;
+
+                path = g_build_filename (dir_path, g_ptr_array_index (names, i), NULL);
+                if (!g_file_get_contents (path, &contents, NULL, NULL))
+                        continue;
+
+                lines = g_strsplit (contents, "\n", -1);
+                for (int j = 0; lines[j] != NULL; j++) {
+                        char *line = g_strstrip (lines[j]);
+                        char *eq;
+
+                        if (line[0] == '\0' || line[0] == '#')
+                                continue;
+                        eq = strchr (line, '=');
+                        if (eq == NULL)
+                                continue;
+                        *eq = '\0';
+                        g_setenv (g_strstrip (line), g_strstrip (eq + 1), TRUE);
+                }
+        }
+}
+
+int
+main (int argc, char **argv)
+{
+        /* argv[1] is the session name passed by leader-main (default "gnome");
+         * the manager needs it to locate the .session file, else it quits with
+         * "Failed to fill session". */
+        const char *session = (argc > 1 && argv[1] != NULL && argv[1][0] != '\0')
+                              ? argv[1] : "gnome";
+        gboolean is_greeter =
+                g_strcmp0 (g_getenv ("XDG_SESSION_CLASS"), "greeter") == 0;
+        /* The greeter is gnome-shell in gdm mode: the login-dialog UI driven
+         * through libgdm/Gdm-1.0 over GDM's private bus. */
+        const char *shell_args_user[] = { "gnome-shell", "--wayland",
+                                          "--display-server", NULL };
+        const char *shell_args_greeter[] = { "gnome-shell", "--mode=gdm",
+                                             "--wayland", "--display-server",
+                                             NULL };
+        const char *const *shell_args =
+                is_greeter ? shell_args_greeter : shell_args_user;
+        const char *manager_args[] = { LIBEXECDIR "/gnome-session-service",
+                                       "--session", session, NULL };
+        GPid shell_pid, manager_pid;
+        GPid gsd_pids[G_N_ELEMENTS (gsd_helpers)];
+        int n_gsd = 0;
+        int i, status;
+        pid_t dead;
+
+        /* Apply ~/.config/environment.d before launching anything: GDM passes
+         * the greeter's whole environment this way, and there is no systemd
+         * user manager here to consume it. Harmless for the user session (its
+         * environment already came from the login shell / wrapper).
+         *
+         * GDM writes the greeter's gdm.conf from the daemon side concurrently
+         * with exec'ing us, so for the greeter wait briefly for it to appear
+         * first -- otherwise we race ahead and read nothing. */
+        if (is_greeter) {
+                const char *home = g_getenv ("HOME");
+                if (home != NULL) {
+                        g_autofree char *conf =
+                                g_build_filename (home, ".config",
+                                                  "environment.d", "gdm.conf",
+                                                  NULL);
+                        for (int w = 0; w < 50 &&
+                             !g_file_test (conf, G_FILE_TEST_EXISTS); w++)
+                                g_usleep (100 * 1000);  /* up to ~5s */
+                }
+        }
+        load_environment_d ();
+        if (g_getenv ("XCURSOR_PATH") == NULL)
+                g_setenv ("XCURSOR_PATH",
+                          "/usr/local/share/icons:/usr/share/icons", FALSE);
+
+        /* 1. Session manager first, so it owns org.gnome.SessionManager before
+         * the shell starts. gnome-shell only wires up its quick-settings / logout
+         * UI if the session manager is already on the bus when it starts; the
+         * manager itself is not graphical and needs no display. */
+        manager_pid = launch (manager_args);
+        if (!wait_for_name ("org.gnome.SessionManager"))
+                g_warning ("org.gnome.SessionManager did not appear; the shell and "
+                           "gsd helpers may fail to register");
+
+        /* 2. Wayland compositor / display server. */
+        shell_pid = launch (shell_args);
+        if (shell_pid == 0) {
+                if (manager_pid != 0)
+                        kill (manager_pid, SIGTERM);
+                return 1;
+        }
+
+        if (!wait_for_wayland ())
+                g_warning ("Wayland display did not appear in time; continuing");
+        g_setenv ("WAYLAND_DISPLAY", "wayland-0", FALSE);
+
+        if (start_xsettings ()) {
+                if (!wait_for_name ("org.gnome.SettingsDaemon.XSettings"))
+                        g_warning ("XSettings did not claim its D-Bus name");
+        }
+
+        /* 3. gnome-settings-daemon helpers (need the manager to stay alive). */
+        for (i = 0; gsd_helpers[i] != NULL; i++) {
+                g_autofree char *bin =
+                        g_strdup_printf (LIBEXECDIR "/gsd-%s", gsd_helpers[i]);
+                if (g_file_test (bin, G_FILE_TEST_IS_EXECUTABLE)) {
+                        const char *args[] = { bin, NULL };
+                        GPid p = launch (args);
+                        if (p != 0)
+                                gsd_pids[n_gsd++] = p;
+                }
+        }
+
+        /* 4. The session ends when EITHER the compositor or the manager exits:
+         * logout quits the manager (it does not stop the shell -- upstream
+         * relies on systemd stopping the session target), and a crash/close
+         * quits the shell. Whichever goes first, tear the rest down so nothing
+         * is left orphaned. */
+        for (;;) {
+                dead = wait (&status);
+                if (dead < 0) {
+                        if (errno == EINTR)
+                                continue;
+                        break;
+                }
+                if (dead == (pid_t) shell_pid || dead == (pid_t) manager_pid)
+                        break;
+                /* a gsd helper died; keep waiting for the shell/manager */
+        }
+
+        if (manager_pid != 0 && dead != (pid_t) manager_pid)
+                kill (manager_pid, SIGTERM);
+        if (dead != (pid_t) shell_pid)
+                kill (shell_pid, SIGTERM);
+        for (i = 0; i < n_gsd; i++)
+                kill (gsd_pids[i], SIGTERM);
+
+        return 0;
+}
