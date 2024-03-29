--- src/pipewire/private.h.orig	2023-10-06 09:37:06 UTC
+++ src/pipewire/private.h
@@ -22,7 +22,8 @@ extern "C" {
 #include <spa/utils/result.h>
 #include <spa/utils/type-info.h>
 
-#if defined(__FreeBSD__) || defined(__MidnightBSD__) || defined(__GNU__)
+#if defined(__FreeBSD__) || defined(__MidnightBSD__) || defined(__GNU__) || \
+    defined(__DragonFly__)
 struct ucred {
 };
 #endif
