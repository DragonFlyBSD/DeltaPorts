--- 3rd/bee.lua/compile/common.lua.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/bee.lua/compile/common.lua	2026-11-08 00:00:00.000000000 +0000
@@ -46,6 +46,10 @@
         crt = "static",
         ldflags = "-Wl,-E",
     },
+    dragonfly = {
+        crt = "static",
+        ldflags = "-Wl,-E",
+    },
     freebsd = {
         crt = "static",
         ldflags = "-Wl,-E",
@@ -98,6 +102,9 @@
     netbsd = {
         defines = "LUA_USE_LINUX",
     },
+    dragonfly = {
+        defines = "LUA_USE_LINUX",
+    },
     freebsd = {
         defines = "LUA_USE_LINUX",
     },
@@ -209,6 +216,13 @@
             "posix",
         }
     },
+    dragonfly = {
+        sysincludes = "%LOCALBASE%/include",
+        sources = need {
+            "bsd",
+            "posix",
+        }
+    },
     freebsd = {
         sysincludes = "%LOCALBASE%/include",
         sources = need {
@@ -291,6 +305,11 @@
     netbsd = {
         links = ":libinotify.a",
         linkdirs = "/usr/pkg/lib",
+        ldflags = "-pthread"
+    },
+    dragonfly = {
+        %LM_INOTIFY_LINK%
+        linkdirs = "%LOCALBASE%/lib",
         ldflags = "-pthread"
     },
     freebsd = {
