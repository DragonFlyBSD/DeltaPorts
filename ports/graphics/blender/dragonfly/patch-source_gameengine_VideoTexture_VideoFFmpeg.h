--- source/gameengine/VideoTexture/VideoFFmpeg.h.orig	2015-03-25 13:01:17.000000000 +0200
+++ source/gameengine/VideoTexture/VideoFFmpeg.h
@@ -33,7 +33,7 @@
 
 #ifdef WITH_FFMPEG
 /* this needs to be parsed with __cplusplus defined before included through ffmpeg_compat.h */
-#if defined(__FreeBSD__)
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #  include <inttypes.h>
 #endif
 extern "C" {
