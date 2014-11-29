--- m4/xbmc_arch.m4.orig	2014-08-17 13:19:05.000000000 +0000
+++ m4/xbmc_arch.m4
@@ -11,7 +11,7 @@ case $host in
   i386-*-freebsd*)
      AC_SUBST(ARCH_DEFINES, "-DTARGET_POSIX -DTARGET_FREEBSD -D_LINUX")
      ;;
-  amd64-*-freebsd*)
+  amd64-*-freebsd*|x86_64-*-dragon*)
      AC_SUBST(ARCH_DEFINES, "-DTARGET_POSIX -DTARGET_FREEBSD -D_LINUX")
      ;;
   arm-apple-darwin*)
