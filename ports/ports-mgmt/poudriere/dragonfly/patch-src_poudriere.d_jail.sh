--- src/poudriere.d/jail.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/jail.sh	2012-11-17 16:19:35.000000000 +0100
@@ -68,7 +68,7 @@
 		err 1 "Unable to remove jail ${JAILNAME}: it is running"
 
 	msg_n "Removing ${JAILNAME} jail..."
-	zfs destroy -r ${JAILFS}
+	zkill ${JAILFS}
 	rmdir ${JAILMNT}
 	rm -rf ${POUDRIERE_DATA}/packages/${JAILNAME}
 	rm -rf ${POUDRIERE_DATA}/cache/${JAILNAME}
@@ -106,22 +106,22 @@
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
@@ -368,7 +368,7 @@
 
 	jail -U root -c path=${JAILMNT} command=/sbin/ldconfig -m /lib /usr/lib /usr/lib/compat
 
-	zfs snapshot ${JAILFS}@clean
+	zsnap ${JAILFS}@clean
 	unset CLEANUP_HOOK
 	msg "Jail ${JAILNAME} ${VERSION} ${ARCH} is ready to be used"
 }
