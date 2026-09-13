--- src/orca/sound.py.orig
+++ src/orca/sound.py
@@ -165,7 +165,7 @@
         if not self._gstreamer_available:
             return
 
-        Gst.init(None)
+        Gst.init([])
 
         self._player = Gst.ElementFactory.make("playbin", "player")
         if self._player is None:
