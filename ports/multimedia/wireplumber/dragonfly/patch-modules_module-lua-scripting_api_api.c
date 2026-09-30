--- modules/module-lua-scripting/api/api.c.orig
+++ modules/module-lua-scripting/api/api.c
@@ -6,6 +6,12 @@
  * SPDX-License-Identifier: MIT
  */
 
+#include <sys/soundcard.h>
+
+#include <errno.h>
+#include <fcntl.h>
+#include <unistd.h>
+
 #include <glib/gstdio.h>
 #include <wp/wp.h>
 #include <pipewire/pipewire.h>
@@ -99,6 +105,140 @@
   { NULL, NULL }
 };
 
+/* DragonFly */
+
+static int
+dragonfly_read_sndstat (lua_State *L)
+{
+  g_autofree gchar *contents = NULL;
+  g_autoptr (GError) error = NULL;
+  gsize length = 0;
+
+  if (!g_file_get_contents ("/dev/sndstat", &contents, &length, &error)) {
+    lua_pushnil (L);
+    lua_pushstring (L, error->message);
+    return 2;
+  }
+
+  lua_pushlstring (L, contents, length);
+  return 1;
+}
+
+static const gchar *
+dragonfly_channel_position (guint channel)
+{
+  switch (channel) {
+  case CHID_L:
+    return "FL";
+  case CHID_R:
+    return "FR";
+  case CHID_C:
+    return "FC";
+  case CHID_LFE:
+    return "LFE";
+  case CHID_LS:
+    return "SL";
+  case CHID_RS:
+    return "SR";
+  case CHID_LR:
+    return "RL";
+  case CHID_RR:
+    return "RR";
+  default:
+    return NULL;
+  }
+}
+
+static int
+dragonfly_get_pcm_layout (lua_State *L)
+{
+  lua_Integer unit = luaL_checkinteger (L, 1);
+  const gchar *stream = luaL_checkstring (L, 2);
+  oss_audioinfo info = { .dev = -1 };
+  unsigned long long order = CHNORDER_UNDEF;
+  g_autofree gchar *position = NULL;
+  gchar path[32];
+  gint open_flags;
+  gint fd;
+
+  if (unit < 0 || unit > 9999)
+    return luaL_argerror (L, 1, "invalid PCM unit");
+
+  if (g_str_equal (stream, "playback"))
+    open_flags = O_WRONLY;
+  else if (g_str_equal (stream, "capture"))
+    open_flags = O_RDONLY;
+  else
+    return luaL_argerror (L, 2, "expected playback or capture");
+
+  g_snprintf (path, sizeof (path), "/dev/dsp%" G_GINT64_FORMAT,
+      (gint64) unit);
+  fd = open (path, open_flags | O_NONBLOCK);
+  if (fd < 0) {
+    lua_pushnil (L);
+    lua_pushstring (L, g_strerror (errno));
+    return 2;
+  }
+
+  if (ioctl (fd, SNDCTL_ENGINEINFO, &info) < 0) {
+    gint error = errno;
+    close (fd);
+    lua_pushnil (L);
+    lua_pushstring (L, g_strerror (error));
+    return 2;
+  }
+
+  if (info.min_channels == info.max_channels && info.max_channels == 1)
+    position = g_strdup ("[MONO]");
+  else if (info.min_channels == info.max_channels && info.max_channels == 2)
+    position = g_strdup ("[FL, FR]");
+  else if (info.min_channels == info.max_channels && info.max_channels > 2 &&
+      info.max_channels <= 16 &&
+      ioctl (fd, SNDCTL_DSP_GET_CHNORDER, &order) == 0) {
+    GString *layout = g_string_new ("[");
+    gboolean complete = TRUE;
+
+    for (guint index = 0; index < (guint) info.max_channels; index++) {
+      const gchar *channel = dragonfly_channel_position (
+          (order >> (index * 4)) & 0xf);
+
+      if (!channel) {
+        complete = FALSE;
+        break;
+      }
+      if (index > 0)
+        g_string_append (layout, ", ");
+      g_string_append (layout, channel);
+    }
+
+    if (complete) {
+      g_string_append_c (layout, ']');
+      position = g_string_free (layout, FALSE);
+    } else {
+      g_string_free (layout, TRUE);
+    }
+  }
+
+  close (fd);
+  if (!position) {
+    lua_pushnil (L);
+    return 1;
+  }
+
+  lua_newtable (L);
+  lua_pushinteger (L, info.max_channels);
+  lua_setfield (L, -2, "channels");
+  lua_pushstring (L, position);
+  lua_setfield (L, -2, "position");
+  return 1;
+}
+
+static const luaL_Reg dragonfly_methods[] = {
+  { "read_sndstat", dragonfly_read_sndstat },
+  { "get_pcm_layout", dragonfly_get_pcm_layout },
+  { NULL, NULL }
+};
+
 /* GSource */
 
 static int
@@ -3056,6 +3196,9 @@
   luaL_newlib (L, glib_methods);
   lua_setglobal (L, "GLib");
 
+  luaL_newlib (L, dragonfly_methods);
+  lua_setglobal (L, "DragonFly");
+
   luaL_newlib (L, i18n_funcs);
   lua_setglobal (L, "I18n");
 
