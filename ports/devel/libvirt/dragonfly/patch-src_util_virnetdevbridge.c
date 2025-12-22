--- src/util/virnetdevbridge.c.orig	2025-12-17 11:26:27 UTC
+++ src/util/virnetdevbridge.c
@@ -44,7 +44,7 @@
 
 #if defined(WITH_BSD_BRIDGE_MGMT)
 # include <net/ethernet.h>
-# include <net/if_bridgevar.h>
+# include <net/bridge/if_bridgevar.h>
 #endif
 
 #define VIR_FROM_THIS VIR_FROM_NONE
@@ -682,7 +682,13 @@ int virNetDevBridgeAddPort(const char *brname,
                            const virNetDevVlan *virtVlan)
 {
     struct ifbreq req = { 0 };
+#if defined(__DragonFly__)
+    struct ifreq ifr;
+    int flags, s;
 
+    memset(&ifr, 0, sizeof(ifr));
+#endif
+
     if (virtVlan) {
         virReportSystemError(ENOSYS, "%s", _("Not supported on this platform"));
         return -1;
@@ -694,6 +700,27 @@ int virNetDevBridgeAddPort(const char *brname,
                              ifname);
         return -1;
     }
+
+#if defined(__DragonFly__)
+    snprintf(ifr.ifr_name, IF_NAMESIZE, "%s", ifname);
+
+    if ((s = socket(AF_LOCAL, SOCK_DGRAM, 0)) < 0) {
+      virReportSystemError(errno, "%s",
+                             _("Cannot open network interface control socket"));
+      return -1;
+    }
+
+    /* Set the interface UP */
+    flags = IFF_UP;
+    ifr.ifr_flags |= flags & 0xFFFF;
+    ifr.ifr_flagshigh |= flags >> 16;
+    if (ioctl(s, SIOCSIFFLAGS, &ifr) < 0) {
+      perror("SIOCSIFFLAGS");
+      close(s);
+      return -1;
+    }
+    close(s);
+#endif
 
     if (virNetDevBridgeCmd(brname, BRDGADD, &req, sizeof(req)) < 0) {
         virReportSystemError(errno,
