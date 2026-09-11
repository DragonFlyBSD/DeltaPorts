--- extra/protobuf/protobuf-24.4/cmake/abseil-cpp.cmake.orig	2026-06-30 16:11:51 UTC
+++ extra/protobuf/protobuf-24.4/cmake/abseil-cpp.cmake
@@ -93,3 +93,17 @@ else()
     absl::scoped_mock_log
   )
 endif ()
+
+# DragonFly: single-pass GNU ld cannot resolve the reference cycles
+# between the bundled static abseil archives (e.g. mutex.cc.o, last in
+# libabsl_synchronization.a, needs earlier members of the same archive;
+# observed as undefined LowLevelAlloc / PerThreadSem / CreateThreadIdentity
+# refs when linking protoc).  Wrap the list in a linker rescan group.
+if(CMAKE_SYSTEM_NAME MATCHES "DragonFly")
+  message(STATUS "DragonFly: wrapping bundled abseil libs in a rescan group")
+  set(protobuf_ABSL_USED_TARGETS
+    "-Wl,--start-group"
+    ${protobuf_ABSL_USED_TARGETS}
+    "-Wl,--end-group"
+  )
+endif()
