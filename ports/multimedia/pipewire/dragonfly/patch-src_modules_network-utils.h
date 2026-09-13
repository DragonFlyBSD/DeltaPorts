--- src/modules/network-utils.h.orig
+++ src/modules/network-utils.h
@@ -6,6 +6,7 @@
 #include <string.h>
 #include <arpa/inet.h>
 #include <net/if.h>
+#include <netinet/in.h>
 #include <errno.h>
 #include <fcntl.h>
 #include <stdlib.h>
@@ -14,7 +15,7 @@
 
 #include <spa/utils/string.h>
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #define ifr_ifindex ifr_index
 #endif
 
