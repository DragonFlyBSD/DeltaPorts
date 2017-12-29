--- src/appleseed/foundation/platform/system.cpp.orig	2017-07-27 15:53:21.000000000 +0000
+++ src/appleseed/foundation/platform/system.cpp	2017-12-29 02:35:39.000000000 +0000
@@ -77,6 +77,9 @@
 // FreeBSD.
 #elif defined __FreeBSD__
 
+// DragonFly.
+#elif defined __DragonFly__
+
     #include <sys/types.h>
     #include <sys/resource.h>
     #include <sys/sysctl.h>
@@ -109,7 +112,9 @@
         "  L3 cache                      size %s, line size %s\n"
         "  physical memory               size %s\n"
         "  virtual memory                size %s",
+#ifdef __DragonFly__
         pretty_uint(get_logical_cpu_core_count()).c_str(),
+#else
         pretty_size(get_l1_data_cache_size()).c_str(),
         pretty_size(get_l1_data_cache_line_size()).c_str(),
         pretty_size(get_l2_cache_size()).c_str(),
@@ -118,6 +123,7 @@
         pretty_size(get_l3_cache_line_size()).c_str(),
         pretty_size(get_total_physical_memory_size()).c_str(),
         pretty_size(get_total_virtual_memory_size()).c_str());
+#endif
 }
 
 size_t System::get_logical_cpu_core_count()
@@ -349,6 +355,13 @@
     return info.resident_size;
 }
 
+// Quick&dirty DragonFly implementation
+#elif defined __DragonFly__
+size_t System::get_l1_data_cache_line_size()
+{
+    return 64;  // on my ironlake netbook, so should be a safe bet
+}
+
 // ------------------------------------------------------------------------------------------------
 // Linux.
 // ------------------------------------------------------------------------------------------------
