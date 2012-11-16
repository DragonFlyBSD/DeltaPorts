#!/bin/sh
#
# set build order: increasing depend numbers, then alphabetical

CONFFILE=/usr/local/etc/dports.conf
TMPFILE=/tmp/queue.awk

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   exit 1
fi

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

AWKCMD='{ \
 n=split($8,a," "); \
 m=split($9,b," "); \
 max=0
 for (j = 1; j <= n; j++) { \
  max++; \
  for (k = 1; k <= m; k++) \
   if (a[j] == b[k]) {\
    b[k] = "";
    break;
   } \
 } \
 for (k = 1; k <= m; k++) \
  if (b[k] != "") max++; \
} \
{ printf("%04d:%s\n",max,substr($2,12)) }'

awk -F \| "${AWKCMD}" ${INDEX} > ${TMPFILE}
sort ${TMPFILE} > ${QUEUE}
rm ${TMPFILE}
