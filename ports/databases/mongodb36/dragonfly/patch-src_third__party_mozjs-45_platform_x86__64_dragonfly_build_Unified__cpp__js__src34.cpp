--- src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src34.cpp.orig	2019-02-01 00:41:50 UTC
+++ src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src34.cpp
@@ -0,0 +1,55 @@
+#define MOZ_UNIFIED_BUILD
+#include "vm/StringBuffer.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/StringBuffer.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/StringBuffer.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/StructuredClone.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/StructuredClone.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/StructuredClone.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/Symbol.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Symbol.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Symbol.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/TaggedProto.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/TaggedProto.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/TaggedProto.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/Time.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/Time.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/Time.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "vm/TypeInference.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "vm/TypeInference.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "vm/TypeInference.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
