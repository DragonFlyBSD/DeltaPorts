--- include/grpc/impl/codegen/port_platform.h.orig	2021-01-19 18:07:57 UTC
+++ include/grpc/impl/codegen/port_platform.h
@@ -335,6 +335,27 @@
 #else /* _LP64 */
 #define GPR_ARCH_32 1
 #endif /* _LP64 */
+#elif defined(__DragonFly__)
+#define GPR_PLATFORM_STRING "dragonfly"
+#ifndef _BSD_SOURCE
+#define _BSD_SOURCE
+#endif
+#define GPR_DRAGONFLY 1
+#define GPR_CPU_POSIX 1
+#define GPR_GCC_ATOMIC 1
+#define GPR_GCC_TLS 1
+#define GPR_POSIX_LOG 1
+#define GPR_POSIX_ENV 1
+#define GPR_POSIX_TMPFILE 1
+#define GPR_POSIX_STAT 1
+#define GPR_POSIX_STRING 1
+#define GPR_POSIX_SUBPROCESS 1
+#define GPR_POSIX_SYNC 1
+#define GPR_POSIX_TIME 1
+#define GPR_HAS_PTHREAD_H 1
+#define GPR_GETPID_IN_UNISTD_H 1
+#define GPR_SUPPORT_CHANNELS_FROM_FD 1
+#define GPR_ARCH_64 1
 #elif defined(__native_client__)
 #define GPR_PLATFORM_STRING "nacl"
 #ifndef _BSD_SOURCE
