--- src/poudriere.d/test_ports.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/test_ports.sh	2012-11-24 12:20:24.000000000 +0100
@@ -69,13 +69,15 @@
 
 test -z ${HOST_PORTDIRECTORY} && test -z ${ORIGIN} && usage
 
+check_jobs
+
 export SKIPSANITY
 
 if [ -z ${ORIGIN} ]; then
 	PORTDIRECTORY=`basename ${HOST_PORTDIRECTORY}`
 else
-	HOST_PORTDIRECTORY=`port_get_base ${PTNAME}`/ports/${ORIGIN}
-	PORTDIRECTORY="/usr/ports/${ORIGIN}"
+	HOST_PORTDIRECTORY=$(get_portsdir ${PTNAME})/${ORIGIN}
+	PORTDIRECTORY="${PORTSRC}/${ORIGIN}"
 fi
 
 test -z "${JAILNAME}" && err 1 "Don't know on which jail to run please specify -j"
@@ -93,19 +95,20 @@
 
 if [ -z ${ORIGIN} ]; then
 	mkdir -p ${JAILMNT}/${PORTDIRECTORY}
-	mount -t nullfs ${HOST_PORTDIRECTORY} ${JAILMNT}/${PORTDIRECTORY}
+	${NULLMOUNT} ${HOST_PORTDIRECTORY} ${JAILMNT}/${PORTDIRECTORY} || \
+	  err 1 "Failed to null-mount ${HOST_PORTDIRECTORY} to jail"
 fi
 
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
@@ -136,7 +139,7 @@
 
 msg "Populating PREFIX"
 mkdir -p ${JAILMNT}${PREFIX}
-injail /usr/sbin/mtree -q -U -f /usr/ports/Templates/BSD.local.dist -d -e -p ${PREFIX} >/dev/null
+injail /usr/sbin/mtree -q -U -f ${PORTSRC}/Templates/BSD.local.dist -d -e -p ${PREFIX} >/dev/null
 
 [ $ZVERSION -lt 28 ] && \
 	find ${JAILMNT}${LOCALBASE}/ -type d | sed "s,^${JAILMNT}${LOCALBASE}/,," | sort > ${JAILMNT}${PREFIX}.PLIST_DIRS.before
