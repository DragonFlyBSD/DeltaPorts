--- 3rd/bee.lua/compile/lua.lua.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/bee.lua/compile/lua.lua	2026-11-08 00:00:00.000000000 +0000
@@ -77,6 +77,10 @@
         defines = "LUA_USE_LINUX",
         links = "m",
     },
+    dragonfly = {
+        defines = "LUA_USE_LINUX",
+        links = "m",
+    },
     freebsd = {
         defines = "LUA_USE_LINUX",
         links = "m",
