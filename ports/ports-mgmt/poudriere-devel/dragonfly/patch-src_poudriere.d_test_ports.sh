--- src/poudriere.d/test_ports.sh.orig	2012-11-14 19:10:09.000000000 +0100
+++ src/poudriere.d/test_ports.sh	2012-11-25 11:35:45.000000000 +0100
@@ -27,6 +27,7 @@
 SETNAME=""
 SKIPSANITY=0
 PTNAME="default"
+check_jobs
 
 while getopts "d:o:cnj:J:p:svz:" FLAG; do
 	case "${FLAG}" in
@@ -70,13 +71,15 @@
 
 test -z ${HOST_PORTDIRECTORY} && test -z ${ORIGIN} && usage
 
+check_jobs
+
 export SKIPSANITY
 
 if [ -z ${ORIGIN} ]; then
 	PORTDIRECTORY=`basename ${HOST_PORTDIRECTORY}`
 else
-	HOST_PORTDIRECTORY=`porttree_get_base ${PTNAME}`/ports/${ORIGIN}
-	PORTDIRECTORY="/usr/ports/${ORIGIN}"
+	HOST_PORTDIRECTORY=$(get_portsdir ${PTNAME})/${ORIGIN}
+	PORTDIRECTORY="${PORTSRC}/${ORIGIN}"
 fi
 
 test -z "${JAILNAME}" && err 1 "Don't know on which jail to run please specify -j"
@@ -94,14 +97,16 @@
 
 if [ -z ${ORIGIN} ]; then
 	mkdir -p ${JAILMNT}/${PORTDIRECTORY}
-	mount -t nullfs ${HOST_PORTDIRECTORY} ${JAILMNT}/${PORTDIRECTORY}
+	${NULLMOUNT} ${HOST_PORTDIRECTORY} ${JAILMNT}/${PORTDIRECTORY} || \
+	  err 1 "Failed to null-mount ${HOST_PORTDIRECTORY} to jail"
 fi
 
+zkill ${JAILFS}@prepkg
+zsnap ${JAILFS}@prepkg
+
 LISTPORTS=$(list_deps ${PORTDIRECTORY} )
 prepare_ports
 
-zfs snapshot ${JAILFS}@prepkg
-
 if ! POUDRIERE_BUILD_TYPE=bulk parallel_build; then
 	failed=$(cat ${JAILMNT}/poudriere/ports.failed | awk '{print $1 ":" $2 }' | xargs echo)
 	skipped=$(cat ${JAILMNT}/poudriere/ports.skipped | awk '{print $1}' | xargs echo)
@@ -119,7 +124,8 @@
 
 zset status "depends:"
 
-zfs destroy -r ${JAILFS}@prepkg
+# This line isn't necessary, it's taken care of in the cleanup
+# zkill ${JAILFS}@prepkg
 
 injail make -C ${PORTDIRECTORY} pkg-depends extract-depends \
 	fetch-depends patch-depends build-depends lib-depends
@@ -150,7 +156,7 @@
 
 msg "Populating PREFIX"
 mkdir -p ${JAILMNT}${PREFIX}
-injail /usr/sbin/mtree -q -U -f /usr/ports/Templates/BSD.local.dist -d -e -p ${PREFIX} >/dev/null
+injail /usr/sbin/mtree -q -U -f ${PORTSRC}/Templates/BSD.local.dist -d -e -p ${PREFIX} >/dev/null
 
 [ $ZVERSION -lt 28 ] && \
 	find ${JAILMNT}${LOCALBASE}/ -type d | sed "s,^${JAILMNT}${LOCALBASE}/,," | sort > ${JAILMNT}${PREFIX}.PLIST_DIRS.before
