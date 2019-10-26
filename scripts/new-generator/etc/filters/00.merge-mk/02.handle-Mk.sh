#!/bin/sh
#
# Handle Mk directory from FreeBSD ports
#

[ -z "${SCRIPTBASE}" ] && err 1 "Fatal error in filter"

. ${SCRIPTBASE}/etc/gen.subr
. ${SCRIPTBASE}/etc/dports.conf

WORKAREA=$(mktemp -d /tmp/workarea.XXXXXXXX)
RET=0

cp -a ${FPORTS}/Mk ${WORKAREA}/

# we don't use bsd.gcc.mk anymore, so remove it to avoid confusion
rm ${WORKAREA}/Mk/bsd.gcc.mk

diffs=$(find ${DELTA}/special/Mk/diffs -name \*\.diff)
for difffile in ${diffs}; do
    echo "Apply patch ${difffile}" 
    if ! patch --quiet -d ${WORKAREA}/Mk -i ${difffile}; then
	echo ${difffile}
	RET=1
    fi
done

find ${WORKAREA}/Mk -name \*\.orig -delete
cpdup -i0 ${WORKAREA}/Mk ${MERGED}/Mk

# Cleanup
rm -fr ${WORKAREA}

return ${RET}
