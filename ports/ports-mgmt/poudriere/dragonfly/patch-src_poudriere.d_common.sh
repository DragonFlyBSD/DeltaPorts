--- src/poudriere.d/common.sh.orig	2012-10-15 18:18:18.000000000 +0200
+++ src/poudriere.d/common.sh	2012-11-17 19:23:27.000000000 +0100
@@ -1,7 +1,5 @@
 #!/bin/sh
 
-# zfs namespace
-NS="poudriere"
 IPS="$(sysctl -n kern.features.inet 2>/dev/null || (sysctl -n net.inet 1>/dev/null 2>&1 && echo 1) || echo 0)$(sysctl -n kern.features.inet6 2>/dev/null || (sysctl -n net.inet6 1>/dev/null 2>&1 && echo 1) || echo 0)"
 
 err() {
@@ -34,6 +32,12 @@
 	esac
 }
 
+if [ -z /sbin/zfs ]; then
+. ${0}.zfs
+else
+. ${0}.nozfs
+fi
+
 log_start() {
 	local logfile=$1
 
@@ -88,26 +92,6 @@
 	fi
 }
 
-zget() {
-	[ $# -ne 1 ] && eargs property
-	zfs get -H -o value ${NS}:${1} ${JAILFS}
-}
-
-zset() {
-	[ $# -ne 2 ] && eargs property value
-	zfs set ${NS}:$1="$2" ${JAILFS}
-}
-
-pzset() {
-	[ $# -ne 2 ] && eargs property value
-	zfs set ${NS}:$1="$2" ${PTFS}
-}
-
-pzget() {
-	[ $# -ne 1 ] && eargs property
-	zfs get -H -o value ${NS}:${1} ${PTFS}
-}
-
 sig_handler() {
 	trap - SIGTERM SIGKILL
 	# Ignore SIGINT while cleaning up
@@ -163,94 +147,17 @@
 	fi
 }
 
-jail_exists() {
-	[ $# -ne 1 ] && eargs jailname
-	zfs list -rt filesystem -H -o ${NS}:type,${NS}:name ${ZPOOL}${ZROOTFS} | \
-		awk -v n=$1 'BEGIN { ret = 1 } $1 == "rootfs" && $2 == n { ret = 0; } END { exit ret }' && return 0
-	return 1
-}
-
 jail_runs() {
 	[ $# -ne 0 ] && eargs
 	jls -qj ${JAILNAME} name > /dev/null 2>&1 && return 0
 	return 1
 }
 
-jail_get_base() {
-	[ $# -ne 1 ] && eargs jailname
-	zfs list -rt filesystem -s name -H -o ${NS}:type,${NS}:name,mountpoint ${ZPOOL}${ZROOTFS} | \
-		awk -v n=$1 '$1 == "rootfs" && $2 == n  { print $3 }' | head -n 1
-}
-
-jail_get_version() {
-	[ $# -ne 1 ] && eargs jailname
-	zfs list -rt filesystem -s name -H -o ${NS}:type,${NS}:name,${NS}:version ${ZPOOL}${ZROOTFS} | \
-		awk -v n=$1 '$1 == "rootfs" && $2 == n { print $3 }' | head -n 1
-}
-
-jail_get_fs() {
-	[ $# -ne 1 ] && eargs jailname
-	zfs list -rt filesystem -s name -H -o ${NS}:type,${NS}:name,name ${ZPOOL}${ZROOTFS} | \
-		awk -v n=$1 '$1 == "rootfs" && $2 == n { print $3 }' | head -n 1
-}
-
-port_exists() {
-	[ $# -ne 1 ] && eargs portstree_name
-	zfs list -t filesystem -H -o ${NS}:type,${NS}:name,name | \
-		awk -v n=$1 'BEGIN { ret = 1 } $1 == "ports" && $2 == n { ret = 0; } END { exit ret }' && return 0
-	return 1
-}
-
-port_get_base() {
-	[ $# -ne 1 ] && eargs portstree_name
-	zfs list -t filesystem -H -o ${NS}:type,${NS}:name,mountpoint | \
-		awk -v n=$1 '$1 == "ports" && $2 == n { print $3 }'
-}
-
-port_get_fs() {
-	[ $# -ne 1 ] && eargs portstree_name
-	zfs list -t filesystem -H -o ${NS}:type,${NS}:name,name | \
-		awk -v n=$1 '$1 == "ports" && $2 == n { print $3 }'
-}
-
-get_data_dir() {
-	local data
-	if [ -n "${POUDRIERE_DATA}" ]; then
-		echo ${POUDRIERE_DATA}
-		return
-	fi
-	data=$(zfs list -rt filesystem -H -o ${NS}:type,mountpoint ${ZPOOL}${ZROOTFS} | awk '$1 == "data" { print $2 }' | head -n 1)
-	if [ -n "${data}" ]; then
-		echo $data
-		return
-	fi
-	zfs create -p -o ${NS}:type=data \
-		-o mountpoint=${BASEFS}/data \
-		${ZPOOL}${ZROOTFS}/data
-	echo "${BASEFS}/data"
-}
-
 fetch_file() {
 	[ $# -ne 2 ] && eargs destination source
 	fetch -p -o $1 $2 || fetch -p -o $1 $2
 }
 
-jail_create_zfs() {
-	[ $# -ne 5 ] && eargs name version arch mountpoint fs
-	local name=$1
-	local version=$2
-	local arch=$3
-	local mnt=$( echo $4 | sed -e "s,//,/,g")
-	local fs=$5
-	msg_n "Creating ${name} fs..."
-	zfs create -p \
-		-o ${NS}:type=rootfs \
-		-o ${NS}:name=${name} \
-		-o ${NS}:version=${version} \
-		-o ${NS}:arch=${arch} \
-		-o mountpoint=${mnt} ${fs} || err 1 " Fail" && echo " done"
-}
-
 jrun() {
 	[ $# -ne 1 ] && eargs network
 	local network=$1
@@ -371,8 +278,8 @@
 	jail_exists ${JAILNAME} || err 1 "No such jail: ${JAILNAME}"
 	jail_runs && err 1 "jail already running: ${JAILNAME}"
 	zset status "start:"
-	zfs destroy -r ${JAILFS}/build 2>/dev/null || :
-	zfs rollback -R ${JAILFS}@clean
+	zkill ${JAILFS}/build 2>/dev/null || :
+	zrollback ${JAILFS}@clean
 
 	msg "Mounting system devices for ${JAILNAME}"
 	do_jail_mounts 1
@@ -419,24 +326,11 @@
 			mdconfig -d -u $dev
 		fi
 	fi
-	zfs rollback -R ${JAILFS%/build/*}@clean
+	zrollback ${JAILFS%/build/*}@clean
 	zset status "idle:"
 	export STATUS=0
 }
 
-port_create_zfs() {
-	[ $# -ne 3 ] && eargs name mountpoint fs
-	local name=$1
-	local mnt=$( echo $2 | sed -e 's,//,/,g')
-	local fs=$3
-	msg_n "Creating ${name} fs..."
-	zfs create -p \
-		-o mountpoint=${mnt} \
-		-o ${NS}:type=ports \
-		-o ${NS}:name=${name} \
-		${fs} || err 1 " Fail" && echo " done"
-}
-
 cleanup() {
 	[ -n "${CLEANED_UP}" ] && return 0
 	msg "Cleaning up"
@@ -464,9 +358,9 @@
 		wait
 	fi
 
-	zfs destroy -r ${JAILFS%/build/*}/build 2>/dev/null || :
-	zfs destroy -r ${JAILFS%/build/*}@prepkg 2>/dev/null || :
-	zfs destroy -r ${JAILFS%/build/*}@preinst 2>/dev/null || :
+	zkill ${JAILFS%/build/*}/build 2>/dev/null || :
+	zkill ${JAILFS%/build/*}@prepkg 2>/dev/null || :
+	zkill ${JAILFS%/build/*}@preinst 2>/dev/null || :
 	jail_stop
 	export CLEANED_UP=1
 }
@@ -509,7 +403,7 @@
 			jail -r ${JAILNAME} >/dev/null
 			jrun 1
 		fi
-		[ "${phase}" = "install" -a $ZVERSION -ge 28 ] && zfs snapshot ${JAILFS}@preinst
+		[ "${phase}" = "install" -a $ZVERSION -ge 28 ] && zsnap ${JAILFS}@preinst
 		if [ "${phase}" = "deinstall" ]; then
 			msg "Checking shared library dependencies"
 			if [ ${PKGNG} -eq 0 ]; then
@@ -554,7 +448,7 @@
 				local mod=$(mktemp ${jailbase}/tmp/mod.XXXXXX)
 				local mod1=$(mktemp ${jailbase}/tmp/mod1.XXXXXX)
 				local die=0
-				zfs diff -FH ${JAILFS}@preinst ${JAILFS} | \
+				zdiff ${JAILFS}@preinst ${JAILFS} | \
 					while read mod type path; do
 					local ppath
 					ppath=`echo "$path" | sed -e "s,^${JAILMNT},," -e "s,^${PREFIX}/,," -e "s,^share/${portname},%%DATADIR%%," -e "s,^etc/${portname},%%ETCDIR%%,"`
@@ -613,7 +507,7 @@
 	jail -r ${JAILNAME} >/dev/null
 	jrun 0
 	zset status "idle:"
-	zfs destroy -r ${JAILFS}@preinst || :
+	zkill ${JAILFS}@preinst || :
 	return 0
 }
 
@@ -639,58 +533,6 @@
 	job_msg "Saved ${port} wrkdir to: ${tarname}"
 }
 
-start_builders() {
-	local arch=$(zget arch)
-	local version=$(zget version)
-	local j mnt fs name
-
-	zfs create -o canmount=off ${JAILFS}/build
-
-	for j in ${JOBS}; do
-		mnt="${JAILMNT}/build/${j}"
-		fs="${JAILFS}/build/${j}"
-		name="${JAILNAME}-job-${j}"
-		zset status "starting_jobs:${j}"
-		mkdir -p "${mnt}"
-		zfs clone -o mountpoint=${mnt} \
-			-o ${NS}:name=${name} \
-			-o ${NS}:type=rootfs \
-			-o ${NS}:arch=${arch} \
-			-o ${NS}:version=${version} \
-			${JAILFS}@prepkg ${fs}
-		zfs snapshot ${fs}@prepkg
-		# Jail might be lingering from previous build. Already recursively
-		# destroyed all the builder datasets, so just try stopping the jail
-		# and ignore any errors
-		jail -r ${name} >/dev/null 2>&1 || :
-		MASTERMNT=${JAILMNT} JAILNAME=${name} JAILMNT=${mnt} JAILFS=${fs} do_jail_mounts 0
-		MASTERMNT=${JAILMNT} JAILNAME=${name} JAILMNT=${mnt} JAILFS=${fs} do_portbuild_mounts 0
-		MASTERMNT=${JAILMNT} JAILNAME=${name} JAILMNT=${mnt} JAILFS=${fs} jrun 0
-		JAILFS=${fs} zset status "idle:"
-	done
-}
-
-stop_builders() {
-	local j mnt
-
-	# wait for the last running processes
-	cat ${JAILMNT}/poudriere/var/run/*.pid 2>/dev/null | xargs pwait 2>/dev/null
-
-	msg "Stopping ${PARALLEL_JOBS} builders"
-
-	for j in ${JOBS}; do
-		jail -r ${JAILNAME}-job-${j} >/dev/null 2>&1 || :
-	done
-
-	mnt=`realpath ${JAILMNT}`
-	mount | awk -v mnt="${mnt}/build/" 'BEGIN{ gsub(/\//, "\\\/", mnt); } { if ($3 ~ mnt && $1 !~ /\/dev\/md/ ) { print $3 }}' |  sort -r | xargs umount -f 2>/dev/null || :
-
-	zfs destroy -r ${JAILFS}/build 2>/dev/null || :
-
-	# No builders running, unset JOBS
-	JOBS=""
-}
-
 build_stats_list() {
 	[ $# -ne 3 ] && eargs html_path type display_name
 	local html_path="$1"
@@ -927,7 +769,7 @@
 
 	job_msg "Starting build of ${port}"
 	zset status "starting:${port}"
-	zfs rollback -r ${JAILFS}@prepkg || err 1 "Unable to rollback ${JAILFS}"
+	zrollback ${JAILFS}@prepkg || err 1 "Unable to rollback ${JAILFS}"
 
 	# If this port is IGNORED, skip it
 	# This is checked here instead of when building the queue
@@ -1381,20 +1223,13 @@
 test -f ${SCRIPTPREFIX}/../../etc/poudriere.conf || err 1 "Unable to find ${SCRIPTPREFIX}/../../etc/poudriere.conf"
 . ${SCRIPTPREFIX}/../../etc/poudriere.conf
 
-[ -z ${ZPOOL} ] && err 1 "ZPOOL variable is not set"
 [ -z ${BASEFS} ] && err 1 "Please provide a BASEFS variable in your poudriere.conf"
 
 trap sig_handler SIGINT SIGTERM SIGKILL
 trap exit_handler EXIT
 trap siginfo_handler SIGINFO
 
-# Test if spool exists
-zpool list ${ZPOOL} >/dev/null 2>&1 || err 1 "No such zpool: ${ZPOOL}"
-ZVERSION=$(zpool list -H -oversion ${ZPOOL})
-# Pool version has now
-if [ "${ZVERSION}" = "-" ]; then
-	ZVERSION=29
-fi
+post_conf_check
 
 : ${SVN_HOST="svn.FreeBSD.org"}
 : ${GIT_URL="git://github.com/freebsd/freebsd-ports.git"}
