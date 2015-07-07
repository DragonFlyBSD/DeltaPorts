--- src/libOpenImageIO/imagebufalgo.cpp.orig	2014-11-25 07:10:44.000000000 +0200
+++ src/libOpenImageIO/imagebufalgo.cpp
@@ -1201,6 +1201,8 @@ static FT_Library ft_library = NULL;
 static bool ft_broken = false;
 #if defined(__linux__) || defined(__FreeBSD__) || defined(__FreeBSD_kernel__)
 const char *default_font_name = "cour";
+#elif defined (__DragonFly__)
+const char *default_font_name = "monospace";
 #elif defined (__APPLE__)
 const char *default_font_name = "Courier New";
 #elif defined (_WIN32)
