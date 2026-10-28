diff --git base/allocator/partition_allocator/src/partition_alloc/spinning_mutex.cc base/allocator/partition_allocator/src/partition_alloc/spinning_mutex.cc
--- base/allocator/partition_allocator/src/partition_alloc/spinning_mutex.cc
+++ base/allocator/partition_allocator/src/partition_alloc/spinning_mutex.cc
@@ -27,6 +27,8 @@
 #include <sys/types.h>
 #include <sys/thr.h>
 #include <sys/umtx.h>
+#elif defined(OS_DRAGONFLY)
+#include <sys/types.h>
 #else
 #include <linux/futex.h>
 #endif
