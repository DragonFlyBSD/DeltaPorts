diff --git base/threading/platform_thread_posix.cc base/threading/platform_thread_posix.cc
index 05506cfd7608..37a34af3cc52 100644
--- base/threading/platform_thread_posix.cc
+++ base/threading/platform_thread_posix.cc
@@ -13,6 +13,10 @@
 #include <sys/types.h>
 #include <unistd.h>
 
+#if defined(OS_DRAGONFLY)
+#include <pthread_np.h>
+#endif
+
 #include <memory>
 #include <tuple>
 
