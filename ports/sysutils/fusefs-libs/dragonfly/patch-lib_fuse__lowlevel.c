--- lib/fuse_lowlevel.c.orig	2016-01-14 21:20:22.000000000 +0200
+++ lib/fuse_lowlevel.c
@@ -2842,7 +2842,7 @@ int fuse_req_getgroups(fuse_req_t req, i
 }
 #endif
 
-#if !defined(__FreeBSD__) && !defined(__NetBSD__)
+#if !defined(__FreeBSD__) && !defined(__NetBSD__) && !defined(__DragonFly__)
 
 static void fill_open_compat(struct fuse_open_out *arg,
 			     const struct fuse_file_info_compat *f)
