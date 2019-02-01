--- src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src33.cpp.orig	2019-02-01 00:41:50 UTC
+++ src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src33.cpp
@@ -0,0 +1,55 @@
+#define MOZ_UNIFIED_BUILD
+#include "vm/SelfHosting.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/SelfHosting.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/SelfHosting.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/Shape.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Shape.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Shape.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/SharedArrayObject.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/SharedArrayObject.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/SharedArrayObject.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/Stack.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Stack.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Stack.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/Stopwatch.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Stopwatch.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Stopwatch.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/String.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/String.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/String.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
