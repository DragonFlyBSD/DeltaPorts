--- src/porting.cpp.orig	2016-04-30 06:49:46 UTC
+++ src/porting.cpp
@@ -249,7 +249,7 @@ void initializePaths(char* argv0)
 	/*
 		OS X
 	*/
-	#elif defined(__APPLE__) || defined(__FreeBSD__)
+	#elif defined(__APPLE__) || defined(__FreeBSD__) || defined(__DragonFly__)
 
 	const int info[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PATHNAME, -1};
 	char* path = NULL;
