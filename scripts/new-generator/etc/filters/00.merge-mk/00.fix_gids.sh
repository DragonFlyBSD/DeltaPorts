#!/bin/sh
#
# Add GIDs to FreeBSD ports GIDs file
#

[ -z "${SCRIPTBASE}" ] && err 1 "Fatal error in filter"

. ${SCRIPTBASE}/etc/gen.subr
. ${SCRIPTBASE}/etc/dports.conf

AWKGID='/nogroup:/ { \
  print "avenger:*:60149:"; \
  print "cbsd:*:60150:"; \
}1'

awk "${AWKGID}" ${FPORTS}/GIDs > ${MERGED}/GIDs
