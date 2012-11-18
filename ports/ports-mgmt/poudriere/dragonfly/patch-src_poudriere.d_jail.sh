--- src/poudriere.d/jail.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/jail.sh	2012-11-17 22:36:28.000000000 +0100
@@ -37,28 +37,11 @@
 	nbs=$(zget stats_skipped|sed -e 's/ //g')
 	nbq=$(zget stats_queued|sed -e 's/ //g')
 	tobuild=$((nbq - nbb - nbf - nbi - nbs))
-	zfs list -H -o ${NS}:type,${NS}:name,${NS}:version,${NS}:arch,${NS}:stats_built,${NS}:stats_failed,${NS}:stats_ignored,${NS}:stats_skipped,${NS}:status,${NS}:method ${JAILFS}| \
-		awk -v q="$nbq" -v tb="$tobuild" '/^rootfs/  {
-			print "Jailname: " $2;
-			print "FreeBSD version: " $3;
-			print "FreeBSD arch: "$4;
-			print "install/update method: "$10;
-			print "Status: "$9;
-			print "Packages built: "$5;
-			print "Packages failed: "$6;
-			print "Packages ignored: "$7;
-			print "Packages skipped: "$8;
-			print "Packages queued: "q;
-			print "Packages to be built: "tb;
-		}'
+	list_jail_info ${nbq} ${tobuild}
 }
 
 list_jail() {
-	[ ${QUIET} -eq 0 ] && \
-		printf '%-20s %-20s %-7s %-7s %-7s %-7s %-7s %-7s %-7s %s\n' "JAILNAME" "VERSION" "ARCH" "METHOD" "SUCCESS" "FAILED" "IGNORED" "SKIPPED" "QUEUED" "STATUS"
-	zfs list -rt filesystem -H \
-		-o ${NS}:type,${NS}:name,${NS}:version,${NS}:arch,${NS}:method,${NS}:stats_built,${NS}:stats_failed,${NS}:stats_ignored,${NS}:stats_skipped,${NS}:stats_queued,${NS}:status ${ZPOOL}${ZROOTFS} | \
-		awk '$1 == "rootfs" { printf("%-20s %-20s %-7s %-7s %-7s %-7s %-7s %-7s %-7s %s\n",$2, $3, $4, $5, $6, $7, $8, $9, $10, $11) }'
+	[ ${QUIET} -eq 0 ] && print_jails_table
 }
 
 delete_jail() {
@@ -68,7 +51,7 @@
 		err 1 "Unable to remove jail ${JAILNAME}: it is running"
 
 	msg_n "Removing ${JAILNAME} jail..."
-	zfs destroy -r ${JAILFS}
+	zkill ${JAILFS}
 	rmdir ${JAILMNT}
 	rm -rf ${POUDRIERE_DATA}/packages/${JAILNAME}
 	rm -rf ${POUDRIERE_DATA}/cache/${JAILNAME}
@@ -106,22 +89,22 @@
 			yes | injail env PAGER=/bin/cat /usr/sbin/freebsd-update install || err 1 "Fail to upgrade system"
 			zset version "${TORELEASE}"
 		fi
-		zfs destroy -r ${JAILFS}@clean
-		zfs snapshot ${JAILFS}@clean
+		zkill ${JAILFS}@clean
+		zsnap ${JAILFS}@clean
 		jail_stop
 		;;
 	csup)
 		msg "Upgrading using csup"
 		install_from_csup
 		yes | make -C ${JAILMNT}/usr/src delete-old delete-old-libs DESTDIR=${JAILMNT}
-		zfs destroy -r ${JAILFS}@clean
-		zfs snapshot ${JAILFS}@clean
+		zkill ${JAILFS}@clean
+		zsnap ${JAILFS}@clean
 		;;
 	svn*)
 		install_from_svn
 		yes | make -C ${JAILMNT} delete-old delete-old-libs DESTDIR=${JAILMNT}
-		zfs destroy -r ${JAILFS}@clean
-		zfs snapshot ${JAILFS}@clean
+		zkill ${JAILFS}@clean
+		zsnap ${JAILFS}@clean
 		;;
 	allbsd)
 		err 1 "Upgrade is not supported with allbsd, to upgrade, please delete and recreate the jail"
@@ -258,12 +241,10 @@
 	test -z ${VERSION} && usage
 
 	if [ -z ${JAILMNT} ]; then
-		[ -z ${BASEFS} ] && err 1 "Please provide a BASEFS variable in your poudriere.conf"
 		JAILMNT=${BASEFS}/jails/${JAILNAME}
 	fi
 
 	if [ -z ${JAILFS} ] ; then
-		[ -z ${ZPOOL} ] && err 1 "Please provide a ZPOOL variable in your poudriere.conf"
 		JAILFS=${ZPOOL}${ZROOTFS}/jails/${JAILNAME}
 	fi
 
@@ -368,7 +349,7 @@
 
 	jail -U root -c path=${JAILMNT} command=/sbin/ldconfig -m /lib /usr/lib /usr/lib/compat
 
-	zfs snapshot ${JAILFS}@clean
+	zsnap ${JAILFS}@clean
 	unset CLEANUP_HOOK
 	msg "Jail ${JAILNAME} ${VERSION} ${ARCH} is ready to be used"
 }
