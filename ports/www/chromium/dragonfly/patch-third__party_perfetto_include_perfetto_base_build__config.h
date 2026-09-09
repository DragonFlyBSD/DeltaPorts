diff --git third_party/perfetto/include/perfetto/base/build_config.h third_party/perfetto/include/perfetto/base/build_config.h
index aabcc8f15972..0b6f7bcfbd4e 100644
--- third_party/perfetto/include/perfetto/base/build_config.h
+++ third_party/perfetto/include/perfetto/base/build_config.h
@@ -64,7 +64,7 @@
 #define PERFETTO_BUILDFLAG_DEFINE_PERFETTO_OS_IOS() 0
 #define PERFETTO_BUILDFLAG_DEFINE_PERFETTO_OS_APPLE_TVOS() 0
 #endif
-#elif defined(__linux__) || defined(__OpenBSD__) || defined(__FreeBSD__)
+#elif defined(__linux__) || defined(__OpenBSD__) || defined(__FreeBSD__) || defined(__DragonFly__)
 #define PERFETTO_BUILDFLAG_DEFINE_PERFETTO_OS_ANDROID() 0
 #define PERFETTO_BUILDFLAG_DEFINE_PERFETTO_OS_LINUX() 1
 #define PERFETTO_BUILDFLAG_DEFINE_PERFETTO_OS_BSD() 1
