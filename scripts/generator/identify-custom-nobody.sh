#!/bin/sh
#
# Identifies which unmaintained ports are customizes so that
# the dragonfly fixes can be added without bureaucracy.

CONFFILE=/usr/local/etc/dports.conf
TMPBASE=/tmp/idnobody.

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

AWKCMD='{ print $1 }'
AWKCMD2='{ print substr($2,12) }'

checkdir FPORTS
checkdir DELTA

cd ${DELTA}/ports && find * -type d -name diffs -print | sed -e 's|/diffs||g' > ${TMPBASE}diffs
cd ${DELTA}/ports && find * -type f -name Makefile.DragonFly -depth 2 -print \
  | sed -e 's|/Makefile.DragonFly||g' > ${TMPBASE}MDF

cat ${TMPBASE}diffs ${TMPBASE}MDF | sort -u > ${TMPBASE}unique

while read line; do

if [ -d ${FPORTS}/${line} ]; then
cd ${FPORTS} && grep -l "^MAINTAINER=.*ports@FreeBSD.org" ${line}/Makefile*
else
echo ">>>>>>>>>>>>>>>>>>>>>>>>> DNE: ${line}"
fi

done < ${TMPBASE}unique

rm ${TMPBASE}*
