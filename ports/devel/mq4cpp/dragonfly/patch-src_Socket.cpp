XXX: what to do with
    // ------ get HW_ADDRESS ------
#ifdef __FreeBSD__
    if (ioctl(sockfd, SIOCGIFMAC, ifr) != 0) continue;  // failed to get mac, skip it
#else
    if (ioctl(sockfd, SIOCGIFHWADDR, ifr) != 0) continue;  // failed to get mac, skip it
#endif


--- src/Socket.cpp.intermediate	2016-08-06 18:32:53 UTC
+++ src/Socket.cpp
@@ -43,6 +43,11 @@
 #include <errno.h>
 #endif
 
+#ifdef __DragonFly__
+#include <cstdlib>
+#include <cstring>
+#endif
+
 int Socket::nofSockets_= 0;
 
 vector<NetAdapter>* Socket::getAdapters()
