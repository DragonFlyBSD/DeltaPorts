#!/bin/sh
#
# Update $MERGED directory (ports + overlay = merged)
#

. /usr/local/etc/dports.conf

TODAY=$(date -j +"%Y%m%d")
UPDATING=${FPORTS}/UPDATING

AWKCMD1='BEGIN { \
  cutoff = today - 10000; \
  ok = 1; \
}{ \
  if ($1 ~ /^20[0-9][0-9][0-1][0-9][0-3][0-9]:/) { \
    datestr = substr($1, 0, 8); \
    if (datestr < cutoff) ok = 0; \
  } \
  if (ok) print $0; \
}'

awk -vtoday=${TODAY} "${AWKCMD1}" ${UPDATING} > ${MERGED}/UPDATING


