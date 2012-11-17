--- src/poudriere.d/ports.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/ports.sh	2012-11-17 16:17:53.000000000 +0100
@@ -116,7 +116,7 @@
 *default delete use-rel-suffix
 ports-all" > ${PTMNT}/csup
 			csup -z -h ${CSUP_HOST} ${PTMNT}/csup || {
-				zfs destroy ${PTFS}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			;;
@@ -127,7 +127,7 @@
 			/usr/sbin/portsnap -d ${PTMNT}/snap -p ${PTMNT}/ports fetch extract || \
 			/usr/sbin/portsnap -d ${PTMNT}/snap -p ${PTMNT}/ports fetch extract || \
 			{
-				zfs destroy ${PTFS}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			;;
@@ -141,7 +141,7 @@
 			msg_n "Checking out the ports tree..."
 			svn -q co ${proto}://${SVN_HOST}/ports/head \
 				${PTMNT} || {
-				zfs destroy ${PTFS}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			echo " done"
@@ -149,7 +149,7 @@
 		git)
 			msg "Cloning the ports tree"
 			git clone ${GIT_URL} ${PTMNT} || {
-				zfs destroy ${PTFS}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			echo " done"
@@ -167,7 +167,7 @@
 		&& err 1 "Ports tree \"${PTNAME}\" is currently mounted and being used."
 	msg "Deleting portstree \"${PTNAME}\""
 	PTFS=$(port_get_fs ${PTNAME})
-	zfs destroy -r ${PTFS}
+	zkill ${PTFS}
 fi
 
 if [ ${UPDATE} -eq 1 ]; then
