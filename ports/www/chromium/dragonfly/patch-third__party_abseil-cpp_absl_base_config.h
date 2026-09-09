diff --git third_party/abseil-cpp/absl/base/config.h third_party/abseil-cpp/absl/base/config.h
index 1c9a3c00c2e7..fc2399f4e8c1 100644
--- third_party/abseil-cpp/absl/base/config.h
+++ third_party/abseil-cpp/absl/base/config.h
@@ -377,7 +377,8 @@ static_assert(ABSL_INTERNAL_INLINE_NAMESPACE_STR[0] != 'h' ||
     defined(__asmjs__) || defined(__EMSCRIPTEN__) || defined(__Fuchsia__) || \
     defined(__sun) || defined(__myriad2__) || defined(__HAIKU__) ||          \
     defined(__OpenBSD__) || defined(__NetBSD__) || defined(__QNX__) ||       \
-    defined(__VXWORKS__) || defined(__hexagon__) || defined(__XTENSA__)
+    defined(__VXWORKS__) || defined(__hexagon__) || defined(__XTENSA__) || \
+    defined(__DragonFly__)
 #define ABSL_HAVE_MMAP 1
 #endif
 
@@ -389,7 +390,7 @@ static_assert(ABSL_INTERNAL_INLINE_NAMESPACE_STR[0] != 'h' ||
 #error ABSL_HAVE_PTHREAD_GETSCHEDPARAM cannot be directly set
 #elif defined(__linux__) || defined(__APPLE__) || defined(__FreeBSD__) || \
     defined(_AIX) || defined(__ros__) || defined(__OpenBSD__) ||          \
-    defined(__NetBSD__) || defined(__VXWORKS__)
+    defined(__NetBSD__) || defined(__VXWORKS__) || defined(__DragonFly__)
 #define ABSL_HAVE_PTHREAD_GETSCHEDPARAM 1
 #endif
 
