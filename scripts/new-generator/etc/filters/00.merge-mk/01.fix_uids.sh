#!/bin/sh
#
# Add UIDs to FreeBSD ports UIDs file
#

[ -z "${SCRIPTBASE}" ] && err 1 "Fatal error in filter"

. ${SCRIPTBASE}/etc/gen.subr
. ${SCRIPTBASE}/etc/dports.conf

AWKUID='/nobody:/ { \
  print "avenger:*:60149:60149::0:0:Mail Avenger:/var/spool/avenger:/usr/sbin/nologin"; \
  print "cbsd:*:60150:150::0:0:Cbsd user:/nonexistent:/bin/sh"; \
}1'

awk "${AWKUID}" ${FPORTS}/UIDs > ${MERGED}/UIDs
