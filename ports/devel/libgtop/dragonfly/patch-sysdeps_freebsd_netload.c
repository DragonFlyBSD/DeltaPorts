--- sysdeps/freebsd/netload.c.orig
+++ sysdeps/freebsd/netload.c
@@ -108,7 +108,11 @@
                         memset(&ifmr, 0, sizeof(ifmr));
                         (void)strlcpy(ifmr.ifm_name, ifa->ifa_name,
                                 sizeof(ifmr.ifm_name));
+#ifndef SIOCGIFXMEDIA
+                        if (
+#else
                         if (ioctl(s, SIOCGIFXMEDIA, (caddr_t)&ifmr) < 0 &&
+#endif
                             ioctl(s, SIOCGIFMEDIA, (caddr_t)&ifmr) < 0) {
                                 glibtop_warn_io_r(server, "ioctl(SIOCGIFMEDIA)");
                         } else {
