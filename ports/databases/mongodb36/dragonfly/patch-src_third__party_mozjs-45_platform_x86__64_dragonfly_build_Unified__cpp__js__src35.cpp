--- src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src35.cpp.orig	2019-02-01 00:41:50 UTC
+++ src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src35.cpp
@@ -0,0 +1,55 @@
+#define MOZ_UNIFIED_BUILD
+#include "vm/TypedArrayObject.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/TypedArrayObject.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/TypedArrayObject.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/UbiNode.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/UbiNode.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/UbiNode.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/UbiNodeCensus.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/UbiNodeCensus.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/UbiNodeCensus.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/UnboxedObject.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/UnboxedObject.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/UnboxedObject.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/Unicode.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Unicode.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Unicode.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/Value.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Value.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Value.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
