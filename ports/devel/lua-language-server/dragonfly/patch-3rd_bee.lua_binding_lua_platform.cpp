--- 3rd/bee.lua/binding/lua_platform.cpp.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/bee.lua/binding/lua_platform.cpp	2026-11-08 00:00:00.000000000 +0000
@@ -28,6 +28,8 @@
         lua_pushstring(L, "linux");
 #elif defined(__NetBSD__)
         lua_pushstring(L, "netbsd");
+#elif defined(__DragonFly__)
+        lua_pushstring(L, "dragonfly");
 #elif defined(__FreeBSD__)
         lua_pushstring(L, "freebsd");
 #elif defined(__OpenBSD__)
