--- libportal/portal.c.orig	2022-11-06 16:55:45.857947000 +0100
+++ libportal/portal.c	2022-11-06 16:58:00.284966000 +0100
@@ -27,7 +27,7 @@
 #include <string.h>
 #include <fcntl.h>
 #include <errno.h>
-#ifndef __FreeBSD__
+#if !defined(__FreeBSD__) && !defined(__DragonFly__)
 #include <sys/vfs.h>
 #endif
 #include <stdio.h>
