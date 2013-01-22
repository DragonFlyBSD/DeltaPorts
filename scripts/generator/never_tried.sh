#!/bin/sh
#
# Takes a tailored list and removes all the entries for which exists a
# build log.  Assume it has already failed.
# also filter out libreoffice

CONFFILE=/usr/local/etc/dports.conf
STARTFILE=/tmp/bulk.list
FINALFILE=/tmp/never.list
LOGDIR=/usr/local/poudriere/data/logs/bulk/master/potential

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

checkdir MERGED

grep -v libreoffice ${STARTFILE} > ${STARTFILE}.real

rm -f ${FINALFILE}
while read line; do
      FOUND=$(grep "|/usr/ports/${line}|" ${INDEX})
      LOGNAME=$(echo ${FOUND} | awk -F \| '{ print $1 }' -).log
      [ ! -f "${LOGDIR}/${LOGNAME}" ] && echo ${line} >> ${FINALFILE}
done < ${STARTFILE}.real
