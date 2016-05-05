--- src/include/sharedLibrary.h.orig	2016-05-05 23:39:19 UTC
+++ src/include/sharedLibrary.h
@@ -52,7 +52,7 @@ inline void* LoadSharedLibrary( std::str
   {
           std::cerr << ::dlerror( ) << std::endl;
   }
-#elif defined(__FreeBSD_kernel__) || defined (__FreeBSD__)
+#elif defined(__FreeBSD_kernel__) || defined (__FreeBSD__) || defined __DragonFly__
         tstring freebsdName = unixPrefix;
         freebsdName += libraryName += ".so";
         void* fileHandle = ::dlopen( freebsdName.c_str( ), RTLD_NOW );
