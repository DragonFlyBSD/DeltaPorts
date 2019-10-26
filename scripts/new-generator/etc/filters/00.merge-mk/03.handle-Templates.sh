#!/bin/sh
#
# Handle Templates directory from FreeBSD ports
#

[ -z "${SCRIPTBASE}" ] && err 1 "Fatal error in filter"

. ${SCRIPTBASE}/etc/gen.subr
. ${SCRIPTBASE}/etc/dports.conf

WORKAREA=$(mktemp -d /tmp/workarea.XXXXXXXX)
RET=0

cp -a ${FPORTS}/Templates ${WORKAREA}/

diffs=$(find ${DELTA}/special/Templates/diffs -name \*\.diff)
for difffile in ${diffs}; do
    echo "Apply patch ${difffile}" 
    if ! patch --quiet -d ${WORKAREA}/Templates -i ${difffile}; then
	echo ${difffile}
	RET=1
    fi
done

find ${WORKAREA}/Templates -name \*\.orig -delete
cpdup -i0 ${WORKAREA}/Templates ${MERGED}/Templates

# Cleanup
rm -fr ${WORKAREA}

return ${RET}
