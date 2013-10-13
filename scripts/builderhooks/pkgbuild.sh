#
# Executed whenever a port builds, fails, or is ignored or skipped
# The RESULT variable distinguishes these events
#
# The "potential" ports tree consists of all merged ports.  The ones that
# build are put into dports.  For now, actions on other port trees are
# ignored

. /usr/local/etc/dports.conf
AWKPKGV='{ n=split($0,ray,"-"); print ray[n] }'
LOGBASE=${POUDRIERE_DATA}/logs/bulk/${MASTERNAME}/latest/logs
DELTAORIGIN=${DELTA}/ports/${ORIGIN}
STATUSFILE=${DELTAORIGIN}/STATUS
PKGV=$(echo ${PKGNAME} | awk "${AWKPKGV}")

write_delta ()
{
    local result=${1}
    NAME=$(echo ${ORIGIN} | sed -e 's|/|__|g')
    mkdir -p -m 777 ${COMQUEUE}
    rm -f ${COMQUEUE}/delta.${NAME}
    cat > ${COMQUEUE}/delta.${NAME} << EOF
${ORIGIN}
${result}
${PKGV}
EOF
    chmod 777 ${COMQUEUE}/delta.${NAME}
}

write_status ()
{
    echo ${1} > ${STATUSFILE}
    echo "Last attempt: ${2}" >> ${STATUSFILE}
    echo "Last success: ${3}" >> ${STATUSFILE}
    chown automaton:automaton ${STATUSFILE}
}

ensure_deltaorigin ()
{
    mkdir -p ${DELTAORIGIN}
    chown automaton:automaton ${DELTAORIGIN}
}

if [ "${PTNAME}" = "potential" ]; then

    if [ "${RESULT}" = "failed" ]; then
	[ -d ${DPORTS}/${ORIGIN} ] && echo "${ORIGIN}" >> ${LOGBASE}/PSF.log    

	ensure_deltaorigin
	if [ -f ${STATUSFILE} ]; then
	    TYPE=$(grep PORT ${STATUSFILE})
	    LASTSUCC=$(grep "Last success:" ${STATUSFILE} | cut -c 15-80)
	    write_status "${TYPE}" "${PKGV}" "${LASTSUCC}"
	else
	    write_status "PORT" "${PKGV}" ""
	fi
	write_delta Failure
    fi

    if [ "${RESULT}" = "success" ]; then
	oldloc=${POTENTIAL}/${ORIGIN}
	newloc=${DPORTS}/${ORIGIN}
	newdir=$(dirname $newloc)
	ensure_deltaorigin
	
	if [ -d ${newloc} ]; then
	    # This means we built it before and a STATUS file should exist
	    # Check to see the version of the previous attempt.
	    if [ -f ${STATUSFILE} ]; then
		LASTSUCC=$(grep "Last success:" ${STATUSFILE} | cut -c 15-80)
		if [ "${PKGV}" = "${LASTSUCC}" ]; then
		    action="Tweak"
		    reflex="version ${PKGV}"
		else
		    action="Update"
		    reflex="to version ${PKGV}"
		fi
	    else
		action="Update"
		reflex="to version ${PKGV}"
	    fi
	else
	    action="Import"
	    reflex="version ${PKGV}"
	fi
	mkdir -p ${newdir}
	rm -rf ${newloc}
	cp -r ${oldloc} ${newloc}
	chown -R automaton:automaton ${newloc}

	NAME=$(echo ${ORIGIN} | sed -e 's|/|__|g')
	mkdir -p -m 777 ${COMQUEUE}
	rm -f ${COMQUEUE}/dport.${NAME}
	cat > ${COMQUEUE}/dport.${NAME} << EOF
${ORIGIN}
${action}
${PKGV}
EOF
	chmod 777 ${COMQUEUE}/dport.${NAME}

	# Now update the STATUSFILE
	if [ -f ${STATUSFILE} ]; then
	    TYPE=$(grep -E "^(PORT|DPORT|LOCK)" ${STATUSFILE})
	    write_status "${TYPE}" "${PKGV}" "${PKGV}"
	else
	    write_status "PORT" "${PKGV}" "${PKGV}"
	fi

	write_delta Success
    fi
fi

