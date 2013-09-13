--- lib/Support/Unix/Path.inc.orig	2013-08-10 00:50:57.000000000 +0000
+++ lib/Support/Unix/Path.inc
@@ -182,6 +182,7 @@ namespace sys  {
 namespace fs {
 #if defined(__FreeBSD__) || defined (__NetBSD__) || defined(__Bitrig__) || \
     defined(__OpenBSD__) || defined(__minix) || defined(__FreeBSD_kernel__) || \
+    defined(__DragonFly__) || \
     defined(__linux__) || defined(__CYGWIN__)
 static int
 test_dir(char buf[PATH_MAX], char ret[PATH_MAX],
@@ -251,6 +252,7 @@ std::string getMainExecutable(const char
       return link_path;
   }
 #elif defined(__FreeBSD__) || defined (__NetBSD__) || defined(__Bitrig__) || \
+      defined(__DragonFly__) || \
       defined(__OpenBSD__) || defined(__minix) || defined(__FreeBSD_kernel__)
   char exe_path[PATH_MAX];
 
