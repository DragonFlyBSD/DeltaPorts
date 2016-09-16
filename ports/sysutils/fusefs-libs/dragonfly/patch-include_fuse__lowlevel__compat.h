--- include/fuse_lowlevel_compat.h.orig	2016-01-14 21:20:22.000000000 +0200
+++ include/fuse_lowlevel_compat.h
@@ -72,7 +72,7 @@ size_t fuse_dirent_size(size_t namelen);
 char *fuse_add_dirent(char *buf, const char *name, const struct stat *stbuf,
 		      off_t off);
 
-#if !defined(__FreeBSD__) && !defined(__NetBSD__)
+#if !defined(__FreeBSD__) && !defined(__NetBSD__) && !defined(__DragonFly__)
 
 #include <sys/statfs.h>
 
