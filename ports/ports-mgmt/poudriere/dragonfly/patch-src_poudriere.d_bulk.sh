--- src/poudriere.d/bulk.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/bulk.sh	2012-11-17 16:20:22.000000000 +0100
@@ -128,7 +128,7 @@
 
 test -z ${PORTTESTING} && echo "DISABLE_MAKE_JOBS=yes" >> ${JAILMNT}/etc/make.conf
 
-zfs snapshot ${JAILFS}@prepkg
+zsnap ${JAILFS}@prepkg
 
 parallel_build
 
