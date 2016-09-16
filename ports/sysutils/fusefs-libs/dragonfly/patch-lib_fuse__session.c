--- lib/fuse_session.c.orig	2016-01-14 21:20:22.000000000 +0200
+++ lib/fuse_session.c
@@ -235,6 +235,6 @@ void fuse_chan_destroy(struct fuse_chan
 	free(ch);
 }
 
-#ifndef __FreeBSD__
+#if !defined(__FreeBSD__) && !defined(__DragonFly__)
 FUSE_SYMVER(".symver fuse_chan_new_compat24,fuse_chan_new@FUSE_2.4");
 #endif
