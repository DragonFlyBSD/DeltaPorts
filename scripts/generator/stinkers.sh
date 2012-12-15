#!/bin/sh
#
# This script determines which unbuilt ports have the most dependencies
# and sends the sorted list to stdout
#

CONFFILE=/usr/local/etc/dports.conf

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   exit 1
fi

checkdir ()
{
   eval "MYDIR=\$$1"
   if [ ! -d ${MYDIR} ]; then
     echo "The $1 directory (${MYDIR}) does not exist."
     exit 1
  fi
}

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

checkdir DELTA
checkdir DPORTS

TMPFILE=/tmp/allports.list
TMPFIL2=/tmp/unbuilt.list
TMPFIL3=/tmp/critical.list
AWKCMD='{ print substr($2, 12) " " $1 }'
AWKCMD2='{ \
  if ( $1 == target ) { \
    pathx = substr($2, 12); \
  } else { \
    n=split($8,a," "); \
    m=split($9,b," "); \
    found = 0; \
    for (j = 1; j <= n; j++) { \
      if (a[j] == target) { \
        found = 1; \
        total += 1; \
        break; \
      } \
    } \
    if (!found) { \
      for (j = 1; j <= m; j++) { \
        if (b[j] == target) { \
          total += 1; \
          break; \
        } \
      } \
    } \
  } \
} END { if (total > 0) printf "%04d: %s\n", total , pathx}'

rm -f ${TMPFIL2}
awk -F \| "${AWKCMD}" ${INDEX} > ${TMPFILE}

split() {
  VAL1=${1}
  VAL2=${2}
}

while read line; do
  split ${line}
  if [ ! -d ${DPORTS}/${VAL1} ]; then
    if [ -f ${DELTA}/ports/${VAL1}/STATUS ]; then
      [ "`head -n 1 ${DELTA}/ports/${VAL1}/STATUS`" = "MASK" ] && testmask=1
    else
      testmask=0
    fi
    if [ ${testmask} -eq 0 ]; then
       echo "${VAL2}" >> ${TMPFIL2}
    fi
  fi
done < ${TMPFILE}

rm -f ${TMPFILE}
rm -f ${TMPFIL3}

while read pkgname; do
  grep "${pkgname}" ${INDEX} | \
    awk -F \| -v target="${pkgname}" "${AWKCMD2}" >> ${TMPFIL3}
done < ${TMPFIL2}

rm -f ${TMPFIL2}
sort -r ${TMPFIL3}
rm -f ${TMPFIL3}
