#!/bin/sh
#
# This scans Makefile* looking for NO_PACKAGE=
# It will mask the port in DeltaPorts with the given reason

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
checkdir FPORTS

cd ${FPORTS}
MYLIST=$(find * -name Makefile\* -exec grep -l NO_PACKAGE= {} \; 2>/dev/null)

cd ${DELTA}/ports
for item in ${MYLIST}; do
   REASON=$(grep NO_PACKAGE= ${FPORTS}/${item})
   itemdir=$(dirname ${item})
   mkdir -p ${itemdir}
   echo "MASK" > ${itemdir}/STATUS
   echo "# ${REASON}" >> ${itemdir}/STATUS
done
