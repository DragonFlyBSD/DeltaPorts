#!/bin/sh
#
# Handle MOVED from FreeBSD ports
#

[ -z "${SCRIPTBASE}" ] && err 1 "Fatal error in filter"

. ${SCRIPTBASE}/etc/gen.subr
. ${SCRIPTBASE}/etc/dports.conf

AWKMOVED='{  FS = "[| ]"; \
if ($1 == "#") \
  print $0; \
else { \
    split($3,a,"-"); \
    if (a[1] > 2012+0) print $0; \
}}'

awk "${AWKMOVED}" ${FPORTS}/MOVED > ${MERGED}/MOVED
