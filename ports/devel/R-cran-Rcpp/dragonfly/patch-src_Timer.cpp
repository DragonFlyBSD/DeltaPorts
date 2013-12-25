--- src/Timer.cpp.orig	2013-10-29 01:24:28.000000000 +0000
+++ src/Timer.cpp
@@ -31,6 +31,8 @@
     #include <time.h>
 #elif defined(sun) || defined(__sun) || defined(_AIX)
     #include <sys/time.h>
+#elif defined(__DragonFly__)
+    #include <time.h>
 #else /* Unsupported OS */
     #error "Rcpp::Timer not supported by your OS."
 #endif
