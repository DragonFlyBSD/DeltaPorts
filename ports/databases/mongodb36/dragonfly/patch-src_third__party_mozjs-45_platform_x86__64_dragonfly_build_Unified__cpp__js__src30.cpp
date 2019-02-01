--- src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src30.cpp.orig	2019-02-01 00:41:50 UTC
+++ src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src30.cpp
@@ -0,0 +1,55 @@
+#define MOZ_UNIFIED_BUILD
+#include "vm/Interpreter.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Interpreter.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Interpreter.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/JSONParser.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/JSONParser.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/JSONParser.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/MemoryMetrics.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/MemoryMetrics.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/MemoryMetrics.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/Monitor.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Monitor.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Monitor.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/NativeObject.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/NativeObject.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/NativeObject.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/ObjectGroup.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/ObjectGroup.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/ObjectGroup.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
