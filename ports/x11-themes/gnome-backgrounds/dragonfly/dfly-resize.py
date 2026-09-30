#!/usr/bin/env python3
# Scale an image down to fit within max dimensions and save it as JPEG.
# Used to turn djxl-decoded PNGs into small wallpapers. GdkPixbuf is part of
# the GNOME stack already present on the build host.
import sys
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

src, dst, max_w, max_h = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
pb = GdkPixbuf.Pixbuf.new_from_file(src)
w, h = pb.get_width(), pb.get_height()
scale = min(max_w / w, max_h / h, 1.0)
if scale < 1.0:
    pb = pb.scale_simple(int(w * scale), int(h * scale), GdkPixbuf.InterpType.BILINEAR)
pb.savev(dst, "jpeg", ["quality"], ["88"])
