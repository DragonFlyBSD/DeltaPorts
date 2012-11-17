--- src/poudriere.d/test_ports.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/test_ports.sh	2012-11-17 16:16:30.000000000 +0100
@@ -99,13 +99,13 @@
 LISTPORTS=$(list_deps ${PORTDIRECTORY} )
 prepare_ports
 
-zfs snapshot ${JAILFS}@prepkg
+zsnap ${JAILFS}@prepkg
 
 POUDRIERE_BUILD_TYPE=bulk parallel_build
 
 zset status "depends:"
 
-zfs destroy -r ${JAILFS}@prepkg
+zkill ${JAILFS}@prepkg
 
 injail make -C ${PORTDIRECTORY} pkg-depends extract-depends \
 	fetch-depends patch-depends build-depends lib-depends
