--- 3rd/bee.lua/bee/sys/file_handle_bsd.cpp.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/bee.lua/bee/sys/file_handle_bsd.cpp	2026-11-08 00:00:00.000000000 +0000
@@ -3,7 +3,7 @@
 #include <sys/file.h>
 #include <unistd.h>

-#if defined(__FreeBSD__)
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #    include <sys/user.h>
 #endif
