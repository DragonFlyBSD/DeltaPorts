--- showtime/play.py.orig
+++ showtime/play.py
@@ -4,2 +4,3 @@
+import os
 from functools import partial
 from typing import Any
@@ -28,7 +28,32 @@
     picture.props.paintable = paintable

     # OpenGL doesn't work on macOS properly
-    if paintable.props.gl_context and system != "Darwin":
+    if system == "DragonFly":
+        converter = Gst.ElementFactory.make("videoconvert")
+        converter.props.n_threads = min(4, os.cpu_count() or 1)
+        capsfilter = Gst.ElementFactory.make("capsfilter")
+        if not converter or not capsfilter:
+            raise RuntimeError("Cannot create DragonFly video conversion elements")
+
+        capsfilter.props.caps = Gst.Caps.from_string("video/x-raw,format=RGBx")
+        video_sink_bin = Gst.Bin.new("dragonfly-video-sink")
+        for element in (converter, capsfilter, paintable_sink):
+            if not video_sink_bin.add(element):
+                raise RuntimeError("Cannot add video element to DragonFly sink bin")
+
+        if not converter.link(capsfilter) or not capsfilter.link(paintable_sink):
+            raise RuntimeError("Cannot link DragonFly video sink bin")
+
+        converter_sink_pad = converter.get_static_pad("sink")
+        if not converter_sink_pad:
+            raise RuntimeError("Cannot get DragonFly converter sink pad")
+
+        ghost_pad = Gst.GhostPad.new("sink", converter_sink_pad)
+        if not ghost_pad or not video_sink_bin.add_pad(ghost_pad):
+            raise RuntimeError("Cannot expose DragonFly video sink pad")
+
+        sink = video_sink_bin
+    elif paintable.props.gl_context and system != "Darwin":
         gl_sink = Gst.ElementFactory.make("glsinkbin")
         gl_sink.props.sink = paintable_sink  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
         sink = gl_sink
