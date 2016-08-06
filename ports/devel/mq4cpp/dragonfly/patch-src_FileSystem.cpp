--- src/FileSystem.cpp.intermediate	2016-08-06 18:32:53 UTC
+++ src/FileSystem.cpp
@@ -36,6 +36,10 @@
 #define IOBUFSIZE 16384
 #endif
 
+#ifdef __DragonFly__
+#include <sys/syslimits.h>
+#endif
+
 FileSystemException::FileSystemException() 
 { 
 #ifdef WIN32
@@ -354,7 +358,7 @@ Directory* Directory::getCurrent()
 	Directory* ret=new Directory(path);
 	ret->search();
 #else
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 	char* ptr=getcwd (NULL, PATH_MAX);
 #else
 	char* ptr=get_current_dir_name();
