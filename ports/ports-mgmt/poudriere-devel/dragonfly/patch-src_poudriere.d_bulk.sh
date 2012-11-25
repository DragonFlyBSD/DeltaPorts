--- src/poudriere.d/bulk.sh.orig	2012-11-14 19:10:09.000000000 +0100
+++ src/poudriere.d/bulk.sh	2012-11-25 01:01:25.000000000 +0100
@@ -33,6 +33,7 @@
 CLEAN_LISTED=0
 ALL=0
 . ${SCRIPTPREFIX}/common.sh
+check_jobs
 
 [ $# -eq 0 ] && usage
 
@@ -94,6 +95,8 @@
 	LISTPORTS="$@"
 fi
 
+check_jobs
+
 export SKIPSANITY
 
 STATUS=0 # out of jail #
@@ -129,7 +132,7 @@
 
 test -z ${PORTTESTING} && echo "DISABLE_MAKE_JOBS=yes" >> ${JAILMNT}/etc/make.conf
 
-zfs snapshot ${JAILFS}@prepkg
+zsnap ${JAILFS}@prepkg
 
 parallel_build || : # Ignore errors as they are handled below
 
@@ -163,14 +166,14 @@
 	fi
 	msg "Creating pkgng repository"
 	zset status "pkgrepo:"
-	injail tar xf /usr/ports/packages/Latest/pkg.txz -C /
-	injail rm -f /usr/ports/packages/repo.txz /usr/ports/packages/repo.sqlite
+	injail tar xf ${STD_PACKAGES}/Latest/pkg.txz -C /
+	injail rm -f ${STD_PACKAGES}/repo.txz ${STD_PACKAGES}/repo.sqlite
 	if [ -n "${PKG_REPO_SIGNING_KEY}" -a -f "${PKG_REPO_SIGNING_KEY}" ]; then
 		install -m 0400 ${PKG_REPO_SIGNING_KEY} ${JAILMNT}/tmp/repo.key
-		injail pkg-static repo /usr/ports/packages/ /tmp/repo.key
+		injail pkg-static repo ${STD_PACKAGES}/ /tmp/repo.key
 		rm -f ${JAILMNT}/tmp/repo.key
 	else
-		injail pkg-static repo /usr/ports/packages/
+		injail pkg-static repo ${STD_PACKAGES}/
 	fi
 else
 	if [ -n "${NO_RESTRICTED}" ]; then
