--- src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src23.cpp.orig	2019-02-01 00:41:50 UTC
+++ src/third_party/mozjs-45/platform/x86_64/dragonfly/build/Unified_cpp_js_src23.cpp
@@ -0,0 +1,55 @@
+#define MOZ_UNIFIED_BUILD
+#include "jsexn.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "jsexn.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "jsexn.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "jsfriendapi.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "jsfriendapi.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "jsfriendapi.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "jsfun.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "jsfun.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "jsfun.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "jsgc.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "jsgc.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "jsgc.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "jsiter.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "jsiter.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "jsiter.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
+#include "jsnativestack.cpp"
+#ifdef PL_ARENA_CONST_ALIGN_MASK
+#error "jsnativestack.cpp uses PL_ARENA_CONST_ALIGN_MASK, so it cannot be built in unified mode."
+#undef PL_ARENA_CONST_ALIGN_MASK
+#endif
+#ifdef INITGUID
+#error "jsnativestack.cpp defines INITGUID, so it cannot be built in unified mode."
+#undef INITGUID
+#endif
