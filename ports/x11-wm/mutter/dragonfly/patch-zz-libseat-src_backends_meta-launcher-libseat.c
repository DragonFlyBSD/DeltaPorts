--- /dev/null
+++ src/backends/meta-launcher-libseat.c
@@ -0,0 +1,316 @@
+/*
+ * Copyright (C) 2013 Red Hat, Inc.
+ * Copyright (C) 2026 DragonFlyBSD DPorts
+ *
+ * This program is free software; you can redistribute it and/or
+ * modify it under the terms of the GNU General Public License as
+ * published by the Free Software Foundation; either version 2 of the
+ * License, or (at your option) any later version.
+ *
+ * This program is distributed in the hope that it will be useful, but
+ * WITHOUT ANY WARRANTY; without even the implied warranty of
+ * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
+ * General Public License for more details.
+ *
+ * You should have received a copy of the GNU General Public License
+ * along with this program; if not, see <http://www.gnu.org/licenses/>.
+ */
+
+/*
+ * MetaLauncher implementation backed by libseat (seatd) instead of the
+ * org.freedesktop.login1 D-Bus API. Provides the same public interface
+ * as the logind implementation in meta-launcher.c; device opening is
+ * exposed through meta_launcher_open_device()/meta_launcher_close_device()
+ * which MetaDevicePool uses in place of login1 TakeDevice/ReleaseDevice.
+ *
+ * Modeled on the libseat session backend of wlroots.
+ */
+
+#include "config.h"
+
+#include "backends/meta-launcher.h"
+
+#include <errno.h>
+#include <glib-unix.h>
+#include <libseat.h>
+#include <string.h>
+#include <unistd.h>
+
+#include "backends/meta-backend-private.h"
+
+enum
+{
+  PROP_0,
+
+  PROP_SESSION_ACTIVE,
+
+  N_PROPS
+};
+
+static GParamSpec *obj_props[N_PROPS];
+
+struct _MetaLauncher
+{
+  GObject parent;
+
+  MetaBackend *backend;
+
+  struct libseat *seat;
+  GSource *seat_source;
+
+  gboolean session_active;
+  gboolean has_initial_enable;
+};
+
+G_DEFINE_FINAL_TYPE (MetaLauncher,
+                     meta_launcher,
+                     G_TYPE_OBJECT)
+
+static void
+sync_active (MetaLauncher *self,
+             gboolean      active)
+{
+  if (active == self->session_active)
+    return;
+
+  self->session_active = active;
+  g_object_notify_by_pspec (G_OBJECT (self),
+                            obj_props[PROP_SESSION_ACTIVE]);
+}
+
+static void
+on_seat_enabled (struct libseat *seat,
+                 void           *user_data)
+{
+  MetaLauncher *self = user_data;
+
+  self->has_initial_enable = TRUE;
+  sync_active (self, TRUE);
+}
+
+static void
+on_seat_disabled (struct libseat *seat,
+                  void           *user_data)
+{
+  MetaLauncher *self = user_data;
+
+  sync_active (self, FALSE);
+  libseat_disable_seat (seat);
+}
+
+static const struct libseat_seat_listener seat_listener = {
+  .enable_seat = on_seat_enabled,
+  .disable_seat = on_seat_disabled,
+};
+
+static gboolean
+on_seat_fd_ready (int          fd,
+                  GIOCondition condition,
+                  gpointer     user_data)
+{
+  MetaLauncher *self = user_data;
+
+  if (condition & (G_IO_ERR | G_IO_HUP))
+    {
+      g_warning ("Lost connection to seatd");
+      self->seat_source = NULL;
+      return G_SOURCE_REMOVE;
+    }
+
+  if (libseat_dispatch (self->seat, 0) < 0)
+    g_warning ("Failed to dispatch libseat events: %s", g_strerror (errno));
+
+  return G_SOURCE_CONTINUE;
+}
+
+static void
+meta_launcher_get_property (GObject    *object,
+                            guint       prop_id,
+                            GValue     *value,
+                            GParamSpec *pspec)
+{
+  MetaLauncher *launcher = META_LAUNCHER (object);
+
+  switch (prop_id)
+    {
+    case PROP_SESSION_ACTIVE:
+      g_value_set_boolean (value, launcher->session_active);
+      break;
+    default:
+      G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
+      break;
+    }
+}
+
+static void
+meta_launcher_dispose (GObject *object)
+{
+  MetaLauncher *launcher = META_LAUNCHER (object);
+
+  if (launcher->seat_source)
+    {
+      g_source_destroy (launcher->seat_source);
+      launcher->seat_source = NULL;
+    }
+
+  if (launcher->seat)
+    {
+      libseat_close_seat (launcher->seat);
+      launcher->seat = NULL;
+    }
+
+  G_OBJECT_CLASS (meta_launcher_parent_class)->dispose (object);
+}
+
+static void
+meta_launcher_class_init (MetaLauncherClass *klass)
+{
+  GObjectClass *object_class = G_OBJECT_CLASS (klass);
+
+  object_class->dispose = meta_launcher_dispose;
+  object_class->get_property = meta_launcher_get_property;
+
+  obj_props[PROP_SESSION_ACTIVE] =
+    g_param_spec_boolean ("session-active", NULL, NULL,
+                          TRUE,
+                          G_PARAM_READABLE |
+                          G_PARAM_STATIC_STRINGS);
+
+  g_object_class_install_properties (object_class, N_PROPS, obj_props);
+}
+
+static void
+meta_launcher_init (MetaLauncher *launcher)
+{
+}
+
+MetaLauncher *
+meta_launcher_new (MetaBackend  *backend,
+                   GError      **error)
+{
+  g_autoptr (MetaLauncher) launcher = NULL;
+  GSource *source;
+
+  launcher = g_object_new (META_TYPE_LAUNCHER, NULL);
+  launcher->backend = backend;
+
+  launcher->seat = libseat_open_seat (&seat_listener, launcher);
+  if (!launcher->seat)
+    {
+      g_set_error (error, G_IO_ERROR, g_io_error_from_errno (errno),
+                   "Failed to open seat via libseat: %s",
+                   g_strerror (errno));
+      return NULL;
+    }
+
+  /* Wait for the initial enable_seat event before the seat is usable. */
+  while (!launcher->has_initial_enable)
+    {
+      if (libseat_dispatch (launcher->seat, -1) < 0)
+        {
+          g_set_error (error, G_IO_ERROR, g_io_error_from_errno (errno),
+                       "Failed to dispatch libseat events: %s",
+                       g_strerror (errno));
+          libseat_close_seat (launcher->seat);
+          launcher->seat = NULL;
+          return NULL;
+        }
+    }
+
+  source = g_unix_fd_source_new (libseat_get_fd (launcher->seat),
+                                 G_IO_IN | G_IO_ERR | G_IO_HUP);
+  g_source_set_callback (source,
+                         G_SOURCE_FUNC (on_seat_fd_ready),
+                         launcher,
+                         NULL);
+  g_source_set_name (source, "[mutter] libseat");
+  g_source_attach (source, NULL);
+  g_source_unref (source);
+  launcher->seat_source = source;
+
+  launcher->session_active = TRUE;
+
+  return g_steal_pointer (&launcher);
+}
+
+gboolean
+meta_launcher_activate_vt (MetaLauncher  *launcher,
+                           signed char    vt,
+                           GError       **error)
+{
+  if (libseat_switch_session (launcher->seat, vt) < 0)
+    {
+      g_set_error (error, G_IO_ERROR, g_io_error_from_errno (errno),
+                   "Failed to switch session via libseat: %s",
+                   g_strerror (errno));
+      return FALSE;
+    }
+
+  return TRUE;
+}
+
+gboolean
+meta_launcher_is_session_active (MetaLauncher *launcher)
+{
+  return launcher->session_active;
+}
+
+gboolean
+meta_launcher_take_control (MetaLauncher  *launcher,
+                            GError       **error)
+{
+  /* libseat_open_seat() already acquired control of the seat. */
+  return TRUE;
+}
+
+const char *
+meta_launcher_get_seat_id (MetaLauncher *launcher)
+{
+  return libseat_seat_name (launcher->seat);
+}
+
+MetaDBusLogin1Session *
+meta_launcher_get_session_proxy (MetaLauncher *launcher)
+{
+  /* There is no login1 session; callers must handle NULL. */
+  return NULL;
+}
+
+MetaBackend *
+meta_launcher_get_backend (MetaLauncher *launcher)
+{
+  return launcher->backend;
+}
+
+int
+meta_launcher_open_device (MetaLauncher  *launcher,
+                           const char    *path,
+                           int           *out_fd,
+                           GError       **error)
+{
+  int device_id;
+  int fd = -1;
+
+  device_id = libseat_open_device (launcher->seat, path, &fd);
+  if (device_id < 0)
+    {
+      g_set_error (error, G_IO_ERROR, g_io_error_from_errno (errno),
+                   "Failed to open device '%s' via libseat: %s",
+                   path, g_strerror (errno));
+      return -1;
+    }
+
+  *out_fd = fd;
+  return device_id;
+}
+
+void
+meta_launcher_close_device (MetaLauncher *launcher,
+                            int           device_id)
+{
+  if (libseat_close_device (launcher->seat, device_id) < 0)
+    {
+      g_warning ("Failed to close libseat device %d: %s",
+                 device_id, g_strerror (errno));
+    }
+}
