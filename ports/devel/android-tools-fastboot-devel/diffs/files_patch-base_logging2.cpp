--- /dev/null	2017-01-25 08:52:40.891905073 +0200
+++ files/patch-base_logging2.cpp
@@ -0,0 +1,13 @@
+--- base/logging.cpp.orig	2017-01-25 08:47:45.000000000 +0200
++++ base/logging.cpp
+@@ -71,6 +71,10 @@
+ #elif defined(_WIN32)
+ #include <windows.h>
+ #elif defined(__DragonFly__)
++#include <sys/param.h>
++#if __DragonFly_version >= 400709
++#include <sys/lwp.h>
++#endif
+ #include <unistd.h>
+ #elif defined(__FreeBSD__)
+ #include <pthread_np.h>
