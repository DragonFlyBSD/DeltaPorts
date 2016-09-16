--- include/fuse_common.h.orig	2016-01-14 21:20:22.000000000 +0200
+++ include/fuse_common.h
@@ -469,7 +469,7 @@ void fuse_remove_signal_handlers(struct
  * ----------------------------------------------------------- */
 
 #if FUSE_USE_VERSION < 26
-#    ifdef __FreeBSD__
+#    if defined(__FreeBSD__) || defined(__DragonFly__)
 #	 if FUSE_USE_VERSION < 25
 #	     error On FreeBSD API version 25 or greater must be used
 #	 endif
