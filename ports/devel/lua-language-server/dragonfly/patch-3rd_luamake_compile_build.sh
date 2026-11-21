--- 3rd/luamake/compile/build.sh.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/luamake/compile/build.sh	2026-11-08 00:00:00.000000000 +0000
@@ -23,6 +23,9 @@
   FreeBSD)
     OS=freebsd
     ;;
+  DragonFly)
+    OS=dragonfly
+    ;;
   OpenBSD)
     OS=openbsd
     ;;
