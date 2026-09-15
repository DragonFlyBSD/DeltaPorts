--- winpr/include/winpr/platform.h.orig
+++ winpr/include/winpr/platform.h
@@ -684,8 +684,8 @@
 // WARNING: *do not* use thread-local storage for new code because it is not portable
 // It is only used for VirtualChannelInit, and all FreeRDP channels use VirtualChannelInitEx
 // The old virtual channel API is only realistically used on Windows where TLS is available
 #if defined(__STDC_VERSION__) && (__STDC_VERSION__ >= 201112L) && \
-    !defined(__STDC_NO_THREADS__) // C11
+    !defined(__STDC_NO_THREADS__) && !defined(__DragonFly__) // C11
 #include <threads.h>
 
 #define WINPR_TLS thread_local
