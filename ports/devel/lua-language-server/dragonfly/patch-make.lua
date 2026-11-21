--- make.lua.orig	2026-11-08 00:00:00.000000000 +0000
+++ make.lua	2026-11-08 00:00:00.000000000 +0000
@@ -53,6 +53,10 @@
     defines = {
         includeCodeFormat and 'CODE_FORMAT' or nil,
     },
+    dragonfly = {
+        crt = "static",
+        ldflags = { "-rdynamic" },
+    },
     linux = {
         crt = "static",
         ldflags = { "-rdynamic" },
