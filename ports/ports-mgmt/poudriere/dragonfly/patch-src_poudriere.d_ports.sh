--- src/poudriere.d/ports.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/ports.sh	2012-11-20 00:05:22.000000000 +0100
@@ -21,11 +21,11 @@
                      them.
     -p name       -- specifies the name of the portstree we workon . If not
                      specified, work on a portstree called \"default\".
-    -f fs         -- FS name (tank/jails/myjail)
+    -f fs         -- FS name (/poudriere/jails/thejail)
     -M mountpoint -- mountpoint
     -m method     -- when used with -c, specify the method used to update the
-                     tree by default it is portsnap, possible usage are
-                     \"csup\", \"portsnap\", \"svn\", \"svn+http\", \"svn+ssh\""
+                     tree.  The default is git, possible usage are \"git\",
+                     \"rsync\""
 
 	exit 1
 }
@@ -36,6 +36,8 @@
 DELETE=0
 LIST=0
 QUIET=0
+METHOD=git
+PTNAME=default
 while getopts "cFudlp:qf:M:m:" FLAG; do
 	case "${FLAG}" in
 		c)
@@ -48,7 +50,7 @@
 			UPDATE=1
 			;;
 		p)
-			PTNAME=${OPTARG}
+			PTNAME=$(echo ${OPTARG} | sed -e 's| |_|g')
 			;;
 		d)
 			DELETE=1
@@ -76,29 +78,16 @@
 
 [ $(( CREATE + UPDATE + DELETE + LIST )) -lt 1 ] && usage
 
-METHOD=${METHOD:-portsnap}
-PTNAME=${PTNAME:-default}
-
-case ${METHOD} in
-csup)
-	[ -z ${CSUP_HOST} ] && err 2 "CSUP_HOST has to be defined in the configuration to use csup"
-	;;
-portsnap);;
-svn+http);;
-svn+ssh);;
-svn);;
-git);;
-*) usage;;
-esac
-
 if [ ${LIST} -eq 1 ]; then
-	[ $QUIET -eq 0 ] && \
-		printf '%-20s %-10s\n' "PORTSTREE" "METHOD"
-	zfs list -t filesystem -H -o ${NS}:type,${NS}:name,${NS}:method | \
-		awk '$1 == "ports" {printf("%-20s %-10s\n",$2,$3) }'
+	print_ports_table ${QUIET}
 else
 	test -z "${PTNAME}" && usage
 fi
+
+if [ "${METHOD}" = "rsync" ] && [ -z "${DPORTS_RSYNC_LOC}" ]; then
+	err 2 "Define RSYNC_DPORTS_LOC in poudriere.conf first!"
+fi
+
 if [ ${CREATE} -eq 1 ]; then
 	# test if it already exists
 	port_exists ${PTNAME} && err 2 "The ports tree ${PTNAME} already exists"
@@ -107,49 +96,20 @@
 	port_create_zfs ${PTNAME} ${PTMNT} ${PTFS}
 	if [ $FAKE -eq 0 ]; then
 		case ${METHOD} in
-		csup)
-			echo "/!\ WARNING /!\ csup is deprecated and will soon be dropped"
-			mkdir ${PTMNT}/db
-			echo "*default prefix=${PTMNT}
-*default base=${PTMNT}/db
-*default release=cvs tag=.
-*default delete use-rel-suffix
-ports-all" > ${PTMNT}/csup
-			csup -z -h ${CSUP_HOST} ${PTMNT}/csup || {
-				zfs destroy ${PTFS}
-				err 1 " Fail"
-			}
-			;;
-		portsnap)
-			mkdir ${PTMNT}/snap
-			msg "Extracting portstree \"${PTNAME}\"..."
-			mkdir ${PTMNT}/ports
-			/usr/sbin/portsnap -d ${PTMNT}/snap -p ${PTMNT}/ports fetch extract || \
-			/usr/sbin/portsnap -d ${PTMNT}/snap -p ${PTMNT}/ports fetch extract || \
-			{
-				zfs destroy ${PTFS}
-				err 1 " Fail"
-			}
-			;;
-		svn*)
-			case ${METHOD} in
-			svn+http) proto="http" ;;
-			svn+ssh) proto="svn+ssh" ;;
-			svn) proto="svn" ;;
-			esac
-
-			msg_n "Checking out the ports tree..."
-			svn -q co ${proto}://${SVN_HOST}/ports/head \
-				${PTMNT} || {
-				zfs destroy ${PTFS}
+		rsync)
+			msg "Cloning the ports tree via rsync"
+			rsync -vaq ${RSYNC_DPORTS_LOC}/ ${PTFS}/ || {
+				kill_port_metadata ${PTNAME}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			echo " done"
 			;;
 		git)
 			msg "Cloning the ports tree"
-			git clone ${GIT_URL} ${PTMNT} || {
-				zfs destroy ${PTFS}
+			git clone --depth 1 ${DPORTS_URL} ${PTFS} || {
+				kill_port_metadata ${PTNAME}
+				zkill ${PTFS}
 				err 1 " Fail"
 			}
 			echo " done"
@@ -162,52 +122,37 @@
 if [ ${DELETE} -eq 1 ]; then
 	port_exists ${PTNAME} || err 2 "No such ports tree ${PTNAME}"
 	PTMNT=$(port_get_base ${PTNAME})
+	PTFS=$(port_get_fs ${PTNAME})
 	[ -d "${PTMNT}/ports" ] && PORTSMNT="${PTMNT}/ports"
-	/sbin/mount -t nullfs | /usr/bin/grep -q "${PORTSMNT:-${PTMNT}} on" \
-		&& err 1 "Ports tree \"${PTNAME}\" is currently mounted and being used."
+	[ -n "$(check_mount ${PTFS})" ] && \
+		err 1 "Ports tree \"${PTNAME}\" is currently mounted and being used."
 	msg "Deleting portstree \"${PTNAME}\""
-	PTFS=$(port_get_fs ${PTNAME})
-	zfs destroy -r ${PTFS}
+	kill_port_metadata ${PTNAME}
+	zkill ${PTFS}
 fi
 
 if [ ${UPDATE} -eq 1 ]; then
 	port_exists ${PTNAME} || err 2 "No such ports tree ${PTNAME}"
 	PTMNT=$(port_get_base ${PTNAME})
-	[ -d "${PTMNT}/ports" ] && PORTSMNT="${PTMNT}/ports"
-	/sbin/mount -t nullfs | /usr/bin/grep -q "${PORTSMNT:-${PTMNT}} on" \
-		&& err 1 "Ports tree \"${PTNAME}\" is currently mounted and being used."
 	PTFS=$(port_get_fs ${PTNAME})
+	[ -d "${PTMNT}/ports" ] && PORTSMNT="${PTMNT}/ports"
+	[ -n "$(check_mount ${PTFS})" ] && \
+		err 1 "Ports tree \"${PTNAME}\" is currently mounted and being used."
 	msg "Updating portstree \"${PTNAME}\""
 	METHOD=$(pzget method)
 	if [ ${METHOD} = "-" ]; then
-		METHOD=portsnap
+		METHOD=git
 		pzset method ${METHOD}
 	fi
 	case ${METHOD} in
-	csup)
-		echo "/!\ WARNING /!\ csup is deprecated and will soon be dropped"
-		[ -z ${CSUP_HOST} ] && err 2 "CSUP_HOST has to be defined in the configuration to use csup"
-		mkdir -p ${PTMNT}/db
-		echo "*default prefix=${PTMNT}
-*default base=${PTMNT}/db
-*default release=cvs tag=.
-*default delete use-rel-suffix
-ports-all" > ${PTMNT}/csup
-		csup -z -h ${CSUP_HOST} ${PTMNT}/csup
-		;;
-	portsnap|"")
-		PSCOMMAND=fetch
-		[ -t 0 ] || PSCOMMAND=cron
-		/usr/sbin/portsnap -d ${PTMNT}/snap -p ${PORTSMNT} ${PSCOMMAND} update
-		;;
-	svn*)
-		msg_n "Updating the ports tree..."
-		svn -q update ${PORTSMNT:-${PTMNT}}
+	rsync)
+		msg "Updating the ports tree via rsync"
+		rsync -vaq --delete ${RSYNC_DPORTS_LOC}/ ${PTFS}/
 		echo " done"
 		;;
 	git)
-		msg "Pulling from ${GIT_URL}"
-		cd ${PORTSMNT:-${PTMNT}} && git pull
+		msg "Pulling from ${DPORTS_URL}"
+		cd ${PTFS} && git pull
 		echo " done"
 		;;
 	*)
