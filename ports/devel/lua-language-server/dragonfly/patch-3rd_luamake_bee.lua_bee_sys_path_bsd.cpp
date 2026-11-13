--- 3rd/luamake/bee.lua/bee/sys/path_bsd.cpp.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/luamake/bee.lua/bee/sys/path_bsd.cpp	2026-11-08 00:00:00.000000000 +0000
@@ -1,7 +1,7 @@
 #include <bee/sys/path.h>
 #include <unistd.h>

-#if defined(__FreeBSD__)
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #    include <sys/param.h>
 #    include <sys/sysctl.h>
 #elif defined(__OpenBSD__)
@@ -9,7 +9,7 @@
 #endif

 namespace bee::sys {
-#if defined(__FreeBSD__)
+#if defined(__FreeBSD__) || defined(__DragonFly__)
     std::optional<fs::path> exe_path() noexcept {
         int name[] = { CTL_KERN, KERN_PROC, KERN_PROC_PATHNAME, -1 };
         char exe[1024];
