#!/bin/sh
#
# set bulk list but exclude known failures

CONFFILE=/usr/local/etc/dports.conf
TMPFILE=/tmp/pre-bulk.list
FINALFILE=/tmp/bulk.list

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   exit 1
fi

checkdir ()
{
   eval "MYDIR=\$$1"
   if [ ! -d ${MYDIR} ]; then
     echo "The $1 directory (${MYDIR}) does not exist."
     rm -f ${BUSYFILE}
     exit 1
  fi
}

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

AWKCMD='{ n=split($1,a,"-") }{ print a[n] }'
# New logic: We only want to pass through ports that have
# versions we haven't tested yet.
# This means versions that don't match last attempt.
AWKFAIL='{ \
if (FNR == 2) { \
  latt = $3; \
  if (passver != latt) { print "GOOD"; exit } \
} \
if (FNR == 3) { print "FAIL" } \
}'
#if (FNR == 3) { \
#  if (NF == 2) { print "FAIL"; exit } \
#  if ($3 == latt) \
#     print "GOOD"; \
#  else \
#     print "FAIL"; \
#}}'

checkdir DELTA
checkdir FPORTS
checkdir MERGED

cd ${MERGED}
find -s * -type d -depth 1 -maxdepth 1  > ${TMPFILE}

cd ${DELTA}/ports
rm -f ${FINALFILE}
while read line; do
   if [ ! -f ${line}/STATUS ]; then
       [ "${line}" != "Tools/scripts" -a "${line}" != "Templates/Licenses" ] && echo ${line} >> ${FINALFILE}
   else
      FOUND=$(grep "|/usr/ports/${line}|" ${INDEX})
      if [ -z "${FOUND}" ]; then
         # This is a DPORT, never omit
         echo ${line} >> ${FINALFILE}
      else
         VERSION=$(echo ${FOUND} | awk -F \| "${AWKCMD}" -)
         VERDICT=$(awk -v passver=${VERSION} "${AWKFAIL}" ${line}/STATUS)
         [ "${VERDICT}" = "GOOD" ] && echo ${line} >> ${FINALFILE} 
      fi
   fi
done < ${TMPFILE}

rm ${TMPFILE}
