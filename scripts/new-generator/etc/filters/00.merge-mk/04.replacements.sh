#!/bin/sh
#
# Handle special/Mk/replacements files
#

[ -z "${SCRIPTBASE}" ] && err 1 "Fatal error in filter"

. ${SCRIPTBASE}/etc/gen.subr
. ${SCRIPTBASE}/etc/dports.conf

dirlist="Uses"

for d in ${dirlist}
do
    # Skip empty dirs
    if ! ls ${DELTA}/special/Mk/replacements/${d}/* >/dev/null 2>&1 ; then
	continue
    fi

    for f in ${DELTA}/special/Mk/replacements/${d}/*
    do
	cp -av ${DELTA}/special/Mk/replacements/${d}/* ${MERGED}/Mk/${d}
    done
done
