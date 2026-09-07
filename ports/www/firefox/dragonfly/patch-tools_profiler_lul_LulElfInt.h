--- tools/profiler/lul/LulElfInt.h.orig	2026-09-04 00:01:32 UTC
+++ tools/profiler/lul/LulElfInt.h
@@ -79,7 +79,7 @@
 
 #endif
 
-#if defined(GP_OS_freebsd)
+#if defined(GP_OS_freebsd) || defined(GP_OS_dragonfly)
 
 #  ifndef ElfW
 #    define ElfW(type) Elf_##type
