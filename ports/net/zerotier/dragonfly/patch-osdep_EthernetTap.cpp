--- osdep/EthernetTap.cpp.orig	2023-09-14 19:09:26 UTC
+++ osdep/EthernetTap.cpp
@@ -39,7 +39,7 @@
 #include "WindowsEthernetTap.hpp"
 #endif // __WINDOWS__
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #include "BSDEthernetTap.hpp"
 #endif // __FreeBSD__
 
@@ -129,7 +129,7 @@ std::shared_ptr<EthernetTap> EthernetTap
 	return std::shared_ptr<EthernetTap>(new WindowsEthernetTap(homePath,mac,mtu,metric,nwid,friendlyName,handler,arg));
 #endif // __WINDOWS__
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 	return std::shared_ptr<EthernetTap>(new BSDEthernetTap(homePath,mac,mtu,metric,nwid,friendlyName,handler,arg));
 #endif // __FreeBSD__
 
