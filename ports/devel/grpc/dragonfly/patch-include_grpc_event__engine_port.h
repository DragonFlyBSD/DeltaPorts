--- include/grpc/event_engine/port.h.orig	2022-06-21 20:39:47 UTC
+++ include/grpc/event_engine/port.h
@@ -20,7 +20,7 @@
 #if defined(GPR_ANDROID) || defined(GPR_LINUX) || defined(GPR_APPLE) ||     \
     defined(GPR_FREEBSD) || defined(GPR_OPENBSD) || defined(GPR_SOLARIS) || \
     defined(GPR_AIX) || defined(GPR_NACL) || defined(GPR_FUCHSIA) ||        \
-    defined(GRPC_POSIX_SOCKET) || defined(GPR_NETBSD)
+    defined(GRPC_POSIX_SOCKET) || defined(GPR_NETBSD) || defined(GPR_DRAGONFLY)
 #define GRPC_EVENT_ENGINE_POSIX
 #include <arpa/inet.h>
 #include <netdb.h>
