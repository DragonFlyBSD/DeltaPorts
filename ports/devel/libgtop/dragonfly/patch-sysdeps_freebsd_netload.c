--- sysdeps/freebsd/netload.c.orig	2015-09-09 18:30:41 UTC
+++ sysdeps/freebsd/netload.c
@@ -70,6 +70,7 @@ void
 glibtop_get_netload_p (glibtop *server, glibtop_netload *buf,
                        const char *interface)
 {
+#ifndef __DragonFly__ /* TODO */
         struct ifaddrs *ifap, *ifa;
 
         memset (buf, 0, sizeof (glibtop_netload));
@@ -192,3 +193,4 @@ glibtop_get_netload_p (glibtop *server,
         }
         freeifaddrs(ifap);
 }
+#endif /* !__DragonFly__ */
