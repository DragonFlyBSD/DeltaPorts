--- src/poudriere.d/ports.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/ports.sh	2012-11-18 02:20:57.000000000 +0100
@@ -92,10 +92,7 @@
 esac
 
 if [ ${LIST} -eq 1 ]; then
-	[ $QUIET -eq 0 ] && \
-		printf '%-20s %-10s\n' "PORTSTREE" "METHOD"
-	zfs list -t filesystem -H -o ${NS}:type,${NS}:name,${NS}:method | \
-		awk '$1 == "ports" {printf("%-20s %-10s\n",$2,$3) }'
+	[ ${QUIET} -eq 0 ] && print_ports_table
 else
 	test -z "${PTNAME}" && usage
 fi
@@ -116,7 +113,7 @@
 *default delete use-rel-suffix
 ports-all" > ${PTMNT}/csup
 			csup -z -h ${CSUP_HOST} ${PTMNT}/csup || {
-				zfs destroy ${PTFS}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			;;
@@ -124,10 +121,10 @@
 			mkdir ${PTMNT}/snap
 			msg "Extracting portstree \"${PTNAME}\"..."
 			mkdir ${PTMNT}/ports
-			/usr/sbin/portsnap -d ${PTMNT}/snap -p ${PTMNT}/ports fetch extract || \
-			/usr/sbin/portsnap -d ${PTMNT}/snap -p ${PTMNT}/ports fetch extract || \
+			portsnap -d ${PTMNT}/snap -p ${PTMNT}/ports fetch extract || \
+			portsnap -d ${PTMNT}/snap -p ${PTMNT}/ports fetch extract || \
 			{
-				zfs destroy ${PTFS}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			;;
@@ -141,7 +138,7 @@
 			msg_n "Checking out the ports tree..."
 			svn -q co ${proto}://${SVN_HOST}/ports/head \
 				${PTMNT} || {
-				zfs destroy ${PTFS}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			echo " done"
@@ -149,7 +146,7 @@
 		git)
 			msg "Cloning the ports tree"
 			git clone ${GIT_URL} ${PTMNT} || {
-				zfs destroy ${PTFS}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			echo " done"
@@ -167,7 +164,7 @@
 		&& err 1 "Ports tree \"${PTNAME}\" is currently mounted and being used."
 	msg "Deleting portstree \"${PTNAME}\""
 	PTFS=$(port_get_fs ${PTNAME})
-	zfs destroy -r ${PTFS}
+	zkill ${PTFS}
 fi
 
 if [ ${UPDATE} -eq 1 ]; then
