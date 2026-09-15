--- src/vnc-connection.vala.orig
+++ src/vnc-connection.vala
@@ -80,16 +80,10 @@
             }
         }

-        private bool _enable_audio = true;
+        private bool _enable_audio = false;
         public bool enable_audio {
             set {
-                var connection = display.get_connection ();
-                _enable_audio = value;
-
-                if (_enable_audio)
-                    connection.audio_enable ();
-                else
-                    connection.audio_disable ();
+                _enable_audio = false;
             }

             get {
@@ -122,13 +116,7 @@
             notify["scale-mode"].connect (scale);

-            var connection = display.get_connection ();
-            connection.set_audio_format (new Vnc.AudioFormat () {
-                frequency = 44100,
-                nchannels = 2
-            });
-            connection.set_audio (new Vnc.AudioPulse ());
-            connection.audio_enable ();
+            // Gtk-VNC PulseAudio support is unavailable on DragonFly.
             authentication_complete.connect (update_display_authenticated);
         }
