--- libpkg/pkg_elf.c.orig	2014-08-26 17:36:03 UTC
+++ libpkg/pkg_elf.c
@@ -90,7 +90,10 @@ filter_system_shlibs(const char *name, c
 
 	/* match /lib, /lib32, /usr/lib and /usr/lib32 */
 	if (strncmp(shlib_path, "/lib", 4) == 0 ||
-	    strncmp(shlib_path, "/usr/lib", 7) == 0)
+#if defined(__DragonFly__)
+	    strncmp(shlib_path, "/usr/lib/gcc", 12) == 0 ||
+#endif
+	    strncmp(shlib_path, "/usr/lib", 8) == 0)
 		return (EPKG_END); /* ignore libs from base */
 
 	if (path != NULL)
