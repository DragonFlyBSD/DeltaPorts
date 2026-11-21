--- 3rd/luamake/bee.lua/bee/net/socket.cpp.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/luamake/bee.lua/bee/net/socket.cpp	2026-11-08 00:00:00.000000000 +0000
@@ -15,7 +15,7 @@
 #    include <unistd.h>
 #    if defined(__APPLE__)
 #        include <sys/ioctl.h>
-#    elif defined(__FreeBSD__) || defined(__OpenBSD__)
+#    elif defined(__FreeBSD__) || defined(__DragonFly__) || defined(__OpenBSD__)
 #        include <sys/socket.h>
 #    endif
 #endif
@@ -126,7 +126,7 @@
         return net_success(ok);
     }

-#if defined(_WIN32) || defined(__APPLE__) || defined(__FreeBSD__) || defined(SO_NOSIGPIPE)
+#if defined(_WIN32) || defined(__APPLE__) || defined(__FreeBSD__) || defined(__DragonFly__) || defined(SO_NOSIGPIPE)
     static void internal_close(fd_t s) noexcept {
 #    if defined(_WIN32)
         auto saved = ::WSAGetLastError();
