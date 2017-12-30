--- src/Utils.cpp.orig	2017-12-06 11:11:19 UTC
+++ src/Utils.cpp
@@ -1543,7 +1543,7 @@ u_int64_t Utils::macaddr_int(const u_int
 
 /* **************************************** */
 
-#if defined(linux) || defined(__FreeBSD__) || defined(__APPLE__)
+#if defined(linux) || defined(__FreeBSD__) || defined(__APPLE__) || defined(__DragonFly__)
 
 void Utils::readMac(char *_ifname, dump_mac_t mac_addr) {
   char ifname[32];
@@ -2257,7 +2257,7 @@ bool Utils::maskHost(bool isLocalIP) {
 /* ****************************************************** */
 
 void Utils::luaCpuLoad(lua_State* vm) {
-#if !defined(__FreeBSD__) && !defined(__NetBSD__) & !defined(__OpenBSD__) && !defined(__APPLE__) && !defined(WIN32)
+#if !defined(__FreeBSD__) && !defined(__NetBSD__) & !defined(__OpenBSD__) && !defined(__APPLE__) && !defined(WIN32) && !defined(__DragonFly__)
   long unsigned int user, nice, system, idle, iowait, irq, softirq;
   FILE *fp;
 
@@ -2277,7 +2277,7 @@ void Utils::luaCpuLoad(lua_State* vm) {
 /* ****************************************************** */
 
 void Utils::luaMeminfo(lua_State* vm) {
-#if !defined(__FreeBSD__) && !defined(__NetBSD__) & !defined(__OpenBSD__) && !defined(__APPLE__) && !defined(WIN32)
+#if !defined(__FreeBSD__) && !defined(__NetBSD__) & !defined(__OpenBSD__) && !defined(__APPLE__) && !defined(WIN32) || !defined(__DragonFly__)
   long unsigned int memtotal = 0, memfree = 0, buffers = 0, cached = 0, sreclaimable = 0, shmem = 0;
   char *line = NULL;
   size_t len;
