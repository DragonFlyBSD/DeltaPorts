# PR https://github.com/dlang/druntime/pull/2850 (accepted)
--- druntime/src/core/sys/posix/locale.d	2019-10-29 01:26:55.000000000 -0700
+++ druntime/src/core/sys/posix/locale.d	2019-11-19 00:06:59.548858000 -0800
@@ -22,7 +22,7 @@
     version = OSXBSDLocale;
 version (NetBSD)
     version = OSXBSDLocale;
-version (DragonflyBSD)
+version (DragonFlyBSD)
     version = OSXBSDLocale;
 
 
