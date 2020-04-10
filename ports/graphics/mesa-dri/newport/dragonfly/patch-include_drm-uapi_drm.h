--- include/drm-uapi/drm.h.orig	2020-02-13 19:08:31 UTC
+++ include/drm-uapi/drm.h
@@ -36,7 +36,7 @@
 #ifndef _DRM_H_
 #define _DRM_H_
 
-#if   defined(__linux__)
+#if defined(__linux__) || defined(__DragonFly__)
 
 #include <linux/types.h>
 #include <asm/ioctl.h>
