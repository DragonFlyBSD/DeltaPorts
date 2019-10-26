#!/bin/sh
#
# Handle Tools directory from FreeBSD ports
#

[ -z "${SCRIPTBASE}" ] && err 1 "Fatal error in filter"

. ${SCRIPTBASE}/etc/gen.subr
. ${SCRIPTBASE}/etc/dports.conf

# Get a fresh copy of the tools
rm -rf ${MERGED}/Tools
cp -a ${FPORTS}/Tools ${MERGED}

# Replace wrong shebangs, note that 'env perl' should be the
# preferred way of calling it
find ${MERGED}/Tools -type f -print0 | \
    xargs -0 sed -i .BAK -E \
	  -e 's|!/usr/bin/perl|!/usr/local/bin/perl|'

# Cleanup sed trails
find ${MERGED}/Tools -name "*.BAK" -delete
