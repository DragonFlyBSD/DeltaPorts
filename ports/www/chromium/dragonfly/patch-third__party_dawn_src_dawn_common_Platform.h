diff --git third_party/dawn/src/dawn/common/Platform.h third_party/dawn/src/dawn/common/Platform.h
index cbc5c7b8b5cd..d4fad0ee23ec 100644
--- third_party/dawn/src/dawn/common/Platform.h
+++ third_party/dawn/src/dawn/common/Platform.h
@@ -60,7 +60,7 @@
 #error "Unsupported Windows platform."
 #endif
 
-#elif defined(__OpenBSD__) || defined(__FreeBSD__)
+#elif defined(__OpenBSD__) || defined(__FreeBSD__) || defined(__DragonFly__)
 #define DAWN_PLATFORM_IS_LINUX 1
 #define DAWN_PLATFORM_IS_BSD 1
 #define DAWN_PLATFORM_IS_POSIX 1
