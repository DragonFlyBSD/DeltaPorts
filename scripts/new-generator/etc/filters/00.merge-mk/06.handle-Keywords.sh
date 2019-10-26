#!/bin/sh
#
# Handle Keywords directory from FreeBSD ports
#

[ -z "${SCRIPTBASE}" ] && err 1 "Fatal error in filter"

. ${SCRIPTBASE}/etc/gen.subr
. ${SCRIPTBASE}/etc/dports.conf

# Make sure we have a fresh copy of Keywords
rm -rf ${MERGED}/Keywords
cp -a ${FPORTS}/Keywords ${MERGED}/
