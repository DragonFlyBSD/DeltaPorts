--- 3rd/bee.lua/compile/bootstrap.lua.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/bee.lua/compile/bootstrap.lua	2026-11-08 00:00:00.000000000 +0000
@@ -24,6 +24,10 @@
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
