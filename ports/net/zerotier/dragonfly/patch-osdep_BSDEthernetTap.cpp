--- osdep/BSDEthernetTap.cpp.orig	2023-09-14 19:09:26 UTC
+++ osdep/BSDEthernetTap.cpp
@@ -359,7 +359,7 @@ void BSDEthernetTap::scanMulticastGroups
 {
 	std::vector<MulticastGroup> newGroups;
 
-#ifndef __OpenBSD__
+#if !defined(__OpenBSD__) && !defined(__DragonFly__)
 	struct ifmaddrs *ifmap = (struct ifmaddrs *)0;
 	if (!getifmaddrs(&ifmap)) {
 		struct ifmaddrs *p = ifmap;
