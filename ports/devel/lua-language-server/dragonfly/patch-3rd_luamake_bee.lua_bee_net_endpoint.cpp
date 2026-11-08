--- 3rd/luamake/bee.lua/bee/net/endpoint.cpp.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/luamake/bee.lua/bee/net/endpoint.cpp	2026-11-08 00:00:00.000000000 +0000
@@ -15,7 +15,7 @@
 #    include <arpa/inet.h>
 #    include <netdb.h>
 #    include <sys/un.h>
-#    if defined(__FreeBSD__) || defined(__OpenBSD__)
+#    if defined(__FreeBSD__) || defined(__DragonFly__) || defined(__OpenBSD__)
 #        include <netinet/in.h>
 #        include <sys/socket.h>
 #    endif
